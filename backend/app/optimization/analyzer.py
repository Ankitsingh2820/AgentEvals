"""Evidence-based optimization recommendations from an agent's recorded runs.

Rules (plan §15): every recommendation carries the observed evidence it is based on, any
estimate states how it was computed and that it is an upper bound or unverified, and the
suggested next step is always an experiment. Nothing here claims an improvement.
"""

import json
import math
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.costs.pricing import find_model_pricing
from app.engine.options import AgentOptions
from app.metrics.stats import percentile
from app.models import AgentVersion, ModelPricing, Run
from app.providers import caching_mode

MIN_RUNS_FOR_CONFIDENCE = 20
# Conservative cacheable-prefix size. The real minimum is model-dependent (512-4096).
MIN_CACHEABLE_TOKENS = 1024


def _confidence(n: int) -> str:
    return "normal" if n >= MIN_RUNS_FOR_CONFIDENCE else "low"


def _rec(type_, title, evidence, impact, basis, suggested_options, n_runs, caveats=()):
    return {
        "type": type_,
        "title": title,
        "evidence": evidence,
        "estimated_impact": impact,
        "basis": basis,
        "caveats": list(caveats),
        "suggested_options": suggested_options,
        "confidence": _confidence(n_runs),
        "next_step": "Apply to create a candidate version, then run an experiment against "
        "the current version on your evaluation dataset.",
    }


def _agent_llm_calls(run: Run):
    return [s.llm_call for s in run.steps if s.llm_call and s.llm_call.purpose == "agent"]


def _executed_tools(run: Run):
    return [s for s in run.steps if s.tool_call and s.tool_call.skip_reason is None]


def analyze(db: Session, version: AgentVersion, runs: list[Run]) -> list[dict[str, Any]]:
    opts = AgentOptions.model_validate(version.options or {})
    n = len(runs)
    if n == 0:
        return []
    recs = []
    total_latency = sum(r.total_latency_ms or 0 for r in runs)

    # 1. Parallel tool execution: several tools requested in one turn but run one by one.
    if not opts.parallel_tool_calls:
        saving_ms, multi_turns, runs_hit = 0.0, 0, set()
        for run in runs:
            by_turn = defaultdict(list)
            for s in _executed_tools(run):
                by_turn[s.turn].append(s.latency_ms)
            for lats in by_turn.values():
                if len(lats) >= 2:
                    multi_turns += 1
                    runs_hit.add(run.id)
                    saving_ms += sum(lats) - max(lats)
        if multi_turns and total_latency and saving_ms / total_latency >= 0.05:
            recs.append(
                _rec(
                    "parallel_tools",
                    "Run independent tool calls in parallel",
                    {
                        "runs_analyzed": n,
                        "runs_with_multi_tool_turns": len(runs_hit),
                        "multi_tool_turns": multi_turns,
                        "observed_sequential_overlap_ms": round(saving_ms, 1),
                    },
                    {
                        "latency_reduction_pct_upper_bound": round(
                            saving_ms / total_latency * 100, 1
                        )
                    },
                    "Sum of (total - slowest) tool latency over turns with 2+ tool calls, "
                    "divided by "
                    "total run latency. Upper bound: assumes the calls are independent and scale "
                    "without contention.",
                    {"parallel_tool_calls": True},
                    n,
                    [
                        "Only valid if the tools in a turn don't depend on each other.",
                        "Rate limits on the tools' backends can erase the gain.",
                    ],
                )
            )

    # 2. Duplicate tool calls: the same tool with the same arguments, again, in one run.
    if not opts.dedupe_tool_calls:
        dup, total_calls, dup_ms = 0, 0, 0.0
        for run in runs:
            seen = set()
            for s in _executed_tools(run):
                total_calls += 1
                key = (
                    s.tool_call.tool_name,
                    json.dumps(s.tool_call.arguments, sort_keys=True, default=str),
                )
                if key in seen:
                    dup += 1
                    dup_ms += s.latency_ms
                seen.add(key)
        if dup:
            recs.append(
                _rec(
                    "dedupe_tools",
                    "Reuse results of identical tool calls within a run",
                    {
                        "tool_calls": total_calls,
                        "duplicate_calls": dup,
                        "duplicate_share_pct": round(dup / total_calls * 100, 1),
                        "latency_in_duplicates_ms": round(dup_ms, 1),
                    },
                    {"tool_calls_avoided_pct": round(dup / total_calls * 100, 1)},
                    "Count of executed tool calls whose tool name and arguments exactly match an "
                    "earlier call in the same run.",
                    {"dedupe_tool_calls": True},
                    n,
                    [
                        "Only safe for tools that return the same result for the same arguments "
                        "within a run (lookups, not clocks or live prices)."
                    ],
                )
            )

    # 3. Tool budget: some runs use far more tool calls than typical ("Search, Search,
    # Search, Answer").
    ok_counts = [r.tool_call_count for r in runs if r.status == "succeeded"]
    runaway = sum("max iterations" in (r.error or "") for r in runs)
    if opts.max_tool_calls is None and ok_counts:
        median, p90 = percentile(ok_counts, 50), percentile(ok_counts, 90)
        heavy = sum(c > max(2 * median, median + 2) for c in ok_counts)
        if runaway or (heavy and heavy / len(ok_counts) >= 0.1):
            budget = max(1, math.ceil(p90))
            recs.append(
                _rec(
                    "tool_budget",
                    f"Cap tool calls per run at {budget}",
                    {
                        "median_tool_calls": median,
                        "p90_tool_calls": p90,
                        "runs_far_above_median": heavy,
                        "runs_hit_iteration_limit": runaway,
                    },
                    {"tool_calls_capped_at": budget},
                    "Budget = p90 of tool calls in succeeded runs. Runs well above the median "
                    "(>2x, or +2) suggest the agent sometimes searches repeatedly.",
                    {"max_tool_calls": budget},
                    n,
                    [
                        "Capping can lower quality on genuinely hard cases; check the per-case "
                        "comparison, not just the average."
                    ],
                )
            )

    # 4. Prompt caching: multi-turn runs re-send a growing prefix on every call. Only for
    # providers where caching is opt-in: OpenAI/Groq cache automatically, so there is no
    # switch to flip, and their usage already shows the tokens that were served from cache.
    if not opts.prompt_caching and caching_mode(version.provider) == "opt_in":
        reusable, new_tokens, runs_hit = 0, 0, 0
        for run in runs:
            calls = _agent_llm_calls(run)
            prev_prompt = 0
            hit = False
            for c in calls:
                prompt = c.input_tokens + c.cache_creation_input_tokens + c.cache_read_input_tokens
                if prev_prompt >= MIN_CACHEABLE_TOKENS:
                    reusable += prev_prompt
                    new_tokens += max(prompt - prev_prompt, 0)
                    hit = True
                else:
                    new_tokens += prompt
                prev_prompt = prompt
            runs_hit += hit
        if reusable:
            impact: dict[str, Any] = {"reusable_prefix_tokens_per_run": round(reusable / n)}
            pricing = find_model_pricing(
                db, version.provider, version.model, datetime.now(UTC).date()
            )
            if (
                pricing
                and pricing.cache_read_price_per_mtok is not None
                and pricing.cache_write_price_per_mtok is not None
            ):
                m = Decimal(1_000_000)
                saved = (
                    Decimal(reusable)
                    / m
                    * (pricing.input_price_per_mtok - pricing.cache_read_price_per_mtok)
                )
                premium = (
                    Decimal(new_tokens)
                    / m
                    * (pricing.cache_write_price_per_mtok - pricing.input_price_per_mtok)
                )
                impact["estimated_saving_per_run"] = str(
                    ((saved - premium) / n).quantize(Decimal("0.000001"))
                )
                impact["currency"] = pricing.currency
            recs.append(
                _rec(
                    "prompt_caching",
                    "Enable prompt caching for multi-turn runs",
                    {"runs_analyzed": n, "runs_with_cacheable_prefix": runs_hit},
                    impact,
                    "Each agent call re-sends the previous call's prompt. Tokens of prefixes of at "
                    f"least {MIN_CACHEABLE_TOKENS} tokens are priced at the cache-read rate "
                    "instead "
                    "of the input rate, minus the cache-write premium on new tokens.",
                    {"prompt_caching": True},
                    n,
                    [
                        "Cache entries expire (5 min default); slow runs may miss.",
                        "The minimum cacheable prefix is model-dependent (512-4096 tokens).",
                    ],
                )
            )

    # 5. Model routing: a share of runs are simple (one call, no tools) yet use this model.
    if opts.routing is None:
        simple = [
            r
            for r in runs
            if r.status == "succeeded" and r.tool_call_count == 0 and len(_agent_llm_calls(r)) == 1
        ]
        cheaper = _cheaper_model(db, version)
        if cheaper and len(simple) / n >= 0.2:
            current = find_model_pricing(
                db, version.provider, version.model, datetime.now(UTC).date()
            )
            impact = {
                "simple_run_share_pct": round(len(simple) / n * 100, 1),
                "cheaper_model": cheaper.model,
            }
            if current:

                def price(p, r):
                    return (
                        Decimal(r.input_tokens) * p.input_price_per_mtok
                        + Decimal(r.output_tokens) * p.output_price_per_mtok
                    ) / 1_000_000

                now = sum((price(current, r) for r in simple), Decimal(0))
                then = sum((price(cheaper, r) for r in simple), Decimal(0))
                if now:
                    impact["simple_runs_cost_reduction_pct_if_repriced"] = round(
                        float((now - then) / now * 100), 1
                    )
            recs.append(
                _rec(
                    "model_routing",
                    f"Route simple requests to {cheaper.model}",
                    {
                        "runs_analyzed": n,
                        "simple_runs": len(simple),
                        "definition": "succeeded with one LLM call and no tool calls",
                    },
                    impact,
                    "The simple runs' recorded tokens re-priced at the cheaper model's list price. "
                    "Cost only: the cheaper model's quality on these requests is unverified.",
                    {
                        "routing": {
                            "mode": "classifier",
                            "classifier_model": cheaper.model,
                            "routes": [
                                {
                                    "label": "simple",
                                    "model": cheaper.model,
                                    "description": "Answerable directly, without tools or "
                                    "multi-step "
                                    "reasoning.",
                                },
                                {
                                    "label": "complex",
                                    "model": version.model,
                                    "description": "Needs tools, research or multi-step reasoning.",
                                },
                            ],
                        }
                    },
                    n,
                    [
                        "The classifier call adds cost and latency to every run.",
                        "A cheaper model can degrade quality: gate on the experiment's quality "
                        "criteria.",
                    ],
                )
            )

    # 6. Reliability issues that no option fixes but that distort every comparison.
    tool_calls = sum(r.tool_call_count for r in runs)
    tool_failures = sum(r.tool_failure_count for r in runs)
    if tool_calls and tool_failures / tool_calls >= 0.1:
        recs.append(
            _rec(
                "tool_failures",
                "Investigate failing tool calls",
                {
                    "tool_calls": tool_calls,
                    "failed": tool_failures,
                    "failure_rate_pct": round(tool_failures / tool_calls * 100, 1),
                },
                {},
                "Failed tool executions / all tool executions.",
                {},
                n,
                ["Each failure costs a model turn to recover from."],
            )
        )
    truncated = sum("max_tokens" in (r.error or "") for r in runs)
    if truncated:
        recs.append(
            _rec(
                "truncation",
                "Raise max_tokens: outputs were cut off",
                {"runs_truncated": truncated, "runs_analyzed": n},
                {},
                "Runs that ended with stop reason max_tokens.",
                {},
                n,
            )
        )
    return recs


def _cheaper_model(db: Session, version: AgentVersion) -> ModelPricing | None:
    """The cheapest other model of the same provider with a current price entry."""
    today = datetime.now(UTC).date()
    current = find_model_pricing(db, version.provider, version.model, today)
    if current is None:
        return None
    cost = current.input_price_per_mtok + current.output_price_per_mtok
    models = set(
        db.scalars(
            select(ModelPricing.model).where(
                ModelPricing.provider == version.provider, ModelPricing.model != version.model
            )
        )
    )
    candidates = [p for m in models if (p := find_model_pricing(db, version.provider, m, today))]
    cheaper = [p for p in candidates if p.input_price_per_mtok + p.output_price_per_mtok < cost]
    return min(
        cheaper, key=lambda p: p.input_price_per_mtok + p.output_price_per_mtok, default=None
    )
