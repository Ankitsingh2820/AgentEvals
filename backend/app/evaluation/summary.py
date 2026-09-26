"""Aggregate an evaluation's runs and verdicts into quality, latency and cost figures."""

from collections import defaultdict
from decimal import Decimal
from typing import Any

from app.metrics.stats import rate, summarize
from app.models import EvaluationResult, Evaluator, Run

BUCKETS = [(0.0, 0.25), (0.25, 0.5), (0.5, 0.75), (0.75, 1.0000001)]


def _distribution(scores: list[float]) -> dict[str, int]:
    return {f"{lo:.2f}-{min(hi, 1.0):.2f}": sum(lo <= s < hi for s in scores) for lo, hi in BUCKETS}


def _summary_dict(values) -> dict[str, Any]:
    return summarize(values).__dict__


def run_quality(results: list[EvaluationResult]) -> tuple[float, bool] | None:
    """(mean score, all passed) over a run's scored verdicts; None if none were scored."""
    ok = [r for r in results if r.status == "ok"]
    if not ok:
        return None
    return sum(r.score for r in ok) / len(ok), all(r.passed for r in ok)


def build_summary(
    runs: list[Run], results: list[EvaluationResult], evaluators: list[Evaluator]
) -> dict[str, Any]:
    by_evaluator: dict[str, list[EvaluationResult]] = defaultdict(list)
    by_run: dict[str, list[EvaluationResult]] = defaultdict(list)
    for r in results:
        by_evaluator[r.evaluator_id].append(r)
        by_run[r.run_id].append(r)

    per_evaluator = {}
    for ev in evaluators:
        rs = by_evaluator[ev.id]
        ok = [r for r in rs if r.status == "ok"]
        scores = [r.score for r in ok]
        per_evaluator[ev.id] = {
            "name": ev.name,
            "version": ev.version,
            "type": ev.type,
            "scored": len(ok),
            "skipped": sum(r.status == "skipped" for r in rs),
            "errors": sum(r.status == "error" for r in rs),
            "score": _summary_dict(scores),
            "pass_rate": rate(sum(bool(r.passed) for r in ok), len(ok)),
            "score_distribution": _distribution(scores),
        }

    # A run's quality is the mean of its scored verdicts; it passes only if every scored
    # verdict passed. Runs with no scored verdict are reported, not counted as zero.
    run_scores, run_passes = [], 0
    for run in runs:
        q = run_quality(by_run[run.id])
        if q is not None:
            run_scores.append(q[0])
            run_passes += q[1]

    succeeded = [r for r in runs if r.status == "succeeded"]
    costed = [r for r in runs if r.cost_status == "complete"]
    judged = [r for r in results if r.judge_model is not None]
    judge_unpriced = sum(
        r.judge_estimated_cost is None and r.judge_input_tokens > 0 for r in judged
    )

    return {
        "cases": len({r.dataset_case_id for r in runs}),
        "runs": len(runs),
        "runs_succeeded": len(succeeded),
        "success_rate": rate(len(succeeded), len(runs)),
        "quality": {
            "runs_scored": len(run_scores),
            "runs_unscored": len(runs) - len(run_scores),
            "score": _summary_dict(run_scores),
            "pass_rate": rate(run_passes, len(run_scores)),
            "score_distribution": _distribution(run_scores),
        },
        "evaluators": per_evaluator,
        "latency_ms": _summary_dict([r.total_latency_ms for r in succeeded]),
        "estimated_cost": _summary_dict([r.estimated_total_cost for r in costed]),
        "estimated_total_cost": str(sum((r.estimated_total_cost for r in costed), Decimal(0)))
        if len(costed) == len(runs)
        else None,
        "runs_without_cost": len(runs) - len(costed),
        "tool_calls": _summary_dict([r.tool_call_count for r in runs]),
        "llm_calls": _summary_dict([r.llm_call_count for r in runs]),
        # Evaluation overhead, reported separately from the agent's own cost.
        "judge_calls": len(judged),
        "judge_estimated_cost": None
        if judge_unpriced
        else str(sum((r.judge_estimated_cost or Decimal(0) for r in judged), Decimal(0))),
        "judge_calls_unpriced": judge_unpriced,
    }
