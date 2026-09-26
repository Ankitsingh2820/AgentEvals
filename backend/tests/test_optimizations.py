"""Phase 5: optimization options in the engine, routing, the analyzer and apply."""

import threading
import time
import uuid
from decimal import Decimal

import pytest

from app.engine.executor import execute_run
from app.models import Agent, AgentVersion, ModelPricing
from app.optimization.analyzer import analyze
from app.providers import register_provider
from app.providers.anthropic_provider import AnthropicProvider
from app.providers.base import LLMRequest, LLMResponse, Message, ToolCallRequest
from app.providers.mock_provider import MockProvider
from app.tools import register_tool

SLEEP = 0.05
_executions: dict[str, int] = {}
_lock = threading.Lock()


def _slow(name):
    @register_tool(
        name,
        f"test tool {name}",
        {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    )
    def tool(query: str) -> str:
        with _lock:
            _executions[name] = _executions.get(name, 0) + 1
        time.sleep(SLEEP)
        return f"{name}:{query}"


for _n in ("test_slow_a", "test_slow_b", "test_slow_c"):
    _slow(_n)


@pytest.fixture(autouse=True)
def _reset():
    _executions.clear()
    yield
    register_provider("mock", MockProvider)


def _version(db, tools=(), options=None, model="mock-model", system="s") -> AgentVersion:
    agent = Agent(name=f"a-{uuid.uuid4()}")
    v = AgentVersion(
        version=1,
        provider="mock",
        model=model,
        system_prompt=system,
        max_tokens=100,
        tools=list(tools),
        options=options or {},
    )
    agent.versions.append(v)
    db.add(agent)
    db.commit()
    return v


class Scripted:
    name = "mock"

    def __init__(self, *responses):
        self.responses, self.requests = list(responses), []

    def complete(self, request):
        self.requests.append(request)
        return self.responses.pop(0)


def resp(text="", calls=(), **kw):
    return LLMResponse(
        text=text,
        tool_calls=list(calls),
        stop_reason="tool_use" if calls else "end_turn",
        input_tokens=10,
        output_tokens=5,
        response_model="m",
        raw_request={},
        raw_response={},
        **kw,
    )


def call(name="test_slow_a", q="x", id_=None):
    return ToolCallRequest(id_ or f"c{uuid.uuid4().hex[:6]}", name, {"query": q})


# --- parallel tools -------------------------------------------------------------------

TOOLS3 = ("test_slow_a", "test_slow_b", "test_slow_c")


def test_parallel_tool_calls_overlap_and_cut_wall_time(db):
    register_provider("mock", lambda: MockProvider(calls_per_turn=3))
    seq = execute_run(db, _version(db, TOOLS3), "go")
    par = execute_run(db, _version(db, TOOLS3, {"parallel_tool_calls": True}), "go")

    for run in (seq, par):
        assert run.status == "succeeded" and run.tool_call_count == 3
        tool_steps = [s for s in run.steps if s.type == "tool_call"]
        assert [s.tool_call.tool_name for s in tool_steps] == list(TOOLS3)  # model's order
        assert {s.turn for s in tool_steps} == {1}
    assert seq.tool_latency_ms >= 3 * SLEEP * 1000
    assert par.tool_latency_ms < 2 * SLEEP * 1000
    a, b = [s for s in par.steps if s.type == "tool_call"][:2]
    assert b.start_offset_ms < a.start_offset_ms + a.latency_ms  # overlapping in time
    assert "test_slow_a:go | test_slow_b:go | test_slow_c:go" in par.final_output


# --- dedupe and budget ----------------------------------------------------------------


def test_dedupe_executes_identical_calls_once(db):
    register_provider(
        "mock",
        lambda: Scripted(
            resp(calls=[call(q="acme"), call(q="acme"), call(q="globex")]),
            resp(calls=[call(q="acme")]),
            resp(text="done"),
        ),
    )
    run = execute_run(db, _version(db, ["test_slow_a"], {"dedupe_tool_calls": True}), "x")

    assert _executions["test_slow_a"] == 2  # acme + globex
    assert run.tool_call_count == 2 and run.tool_calls_skipped == 2
    skipped = [s for s in run.steps if s.tool_call and s.tool_call.skip_reason]
    assert [s.tool_call.skip_reason for s in skipped] == ["duplicate", "duplicate"]
    assert all(s.tool_call.result == "test_slow_a:acme" for s in skipped)


def test_tool_budget_refuses_extra_calls_with_error_result(db):
    provider = Scripted(resp(calls=[call(q="1"), call(q="2"), call(q="3")]), resp(text="ok"))
    register_provider("mock", lambda: provider)
    run = execute_run(db, _version(db, ["test_slow_a"], {"max_tool_calls": 1}), "x")

    assert run.status == "succeeded"
    assert run.tool_call_count == 1 and run.tool_calls_skipped == 2
    fed_back = provider.requests[1].messages[-1].tool_results
    assert [r.is_error for r in fed_back] == [False, True, True]
    assert "budget exhausted" in fed_back[1].content


# --- prompt caching -------------------------------------------------------------------


def test_prompt_caching_flag_reaches_provider_and_usage_is_recorded(db):
    run = execute_run(db, _version(db, ["test_slow_a"], {"prompt_caching": True}), "go")
    assert run.cache_read_input_tokens > 0 and run.input_tokens == 0  # simulated cache
    plain = execute_run(db, _version(db, ["test_slow_a"]), "go")
    assert plain.cache_read_input_tokens == 0


class _FakeMessages:
    def create(self, **params):
        self.params = params
        from anthropic.types import Message as M

        return M.model_validate(
            {
                "id": "m",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-5",
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        )


def test_anthropic_adapter_sends_cache_control_only_when_asked():
    class Client:
        messages = _FakeMessages()

    p = AnthropicProvider(client=Client())
    req = dict(model="claude-opus-5", system="s", messages=[Message("user", "hi")], max_tokens=10)
    p.complete(LLMRequest(**req, cache=True))
    assert Client.messages.params["cache_control"] == {"type": "ephemeral"}
    p.complete(LLMRequest(**req))
    assert "cache_control" not in Client.messages.params


# --- routing --------------------------------------------------------------------------

RULES = {
    "routing": {
        "mode": "rules",
        "routes": [{"label": "short", "model": "mock-small", "max_input_chars": 20}],
    }
}


def test_rules_routing_picks_model_by_input(db):
    v = _version(db, options=RULES)
    short = execute_run(db, v, "Acme")
    long = execute_run(db, v, "Please research Umbrella Logistics in depth")
    assert (short.route, short.routed_model) == ("short", "mock-small")
    assert short.steps[0].llm_call.model == "mock-small"
    assert (long.route, long.routed_model) == ("default", "mock-model")


CLASSIFIER = {
    "routing": {
        "mode": "classifier",
        "classifier_model": "mock-router",
        "routes": [
            {"label": "simple", "model": "mock-small", "description": "easy"},
            {"label": "complex", "model": "mock-model", "description": "hard"},
        ],
    }
}


def test_classifier_routing_is_traced_and_costed(db):
    for model in ("mock-router", "mock-small"):
        db.add(
            ModelPricing(
                provider="mock",
                model=model,
                input_price_per_mtok=Decimal("1"),
                output_price_per_mtok=Decimal("1"),
                effective_date=__import__("datetime").date(2026, 1, 1),
            )
        )
    db.commit()
    register_provider("mock", lambda: MockProvider(route_label="simple"))
    run = execute_run(db, _version(db, options=CLASSIFIER), "Acme")

    first = run.steps[0]
    assert (first.turn, first.llm_call.purpose, first.llm_call.model) == (
        0,
        "routing",
        "mock-router",
    )
    assert run.routed_model == "mock-small" and run.route == "simple"
    assert run.llm_call_count == 2
    assert run.cost_status == "complete"
    assert first.llm_call.estimated_cost > 0  # routing overhead is part of the run's cost


def test_broken_classifier_falls_back_to_default_model(db):
    register_provider("mock", lambda: Scripted(resp(text="not json"), resp(text="answer")))
    run = execute_run(db, _version(db, options=CLASSIFIER), "Acme")
    assert run.status == "succeeded"
    assert (run.route, run.routed_model) == ("fallback", "mock-model")


@pytest.mark.parametrize(
    "options",
    [
        {
            "routing": {
                "mode": "classifier",
                "routes": [{"label": "a", "model": "m"}, {"label": "b", "model": "n"}],
            }
        },
        {
            "routing": {
                "mode": "rules",
                "routes": [{"label": "a", "model": "m"}, {"label": "a", "model": "n"}],
            }
        },
        {
            "routing": {
                "mode": "rules",
                "routes": [{"label": "a", "model": "m", "input_regex": "("}],
            }
        },
        {"max_tool_calls": -1},
        {"turbo": True},
    ],
)
def test_invalid_options_rejected(client, options):
    r = client.post(
        "/agents", json={"name": "x", "provider": "mock", "model": "m", "options": options}
    )
    assert r.status_code == 422


# --- analyzer -------------------------------------------------------------------------


def test_analyzer_finds_parallelizable_turns(db):
    register_provider("mock", lambda: MockProvider(calls_per_turn=3))
    v = _version(db, TOOLS3)
    runs = [execute_run(db, v, f"q{i}") for i in range(3)]
    recs = {r["type"]: r for r in analyze(db, v, runs)}
    rec = recs["parallel_tools"]
    assert rec["evidence"]["multi_tool_turns"] == 3
    assert 30 < rec["estimated_impact"]["latency_reduction_pct_upper_bound"] < 100
    assert rec["suggested_options"] == {"parallel_tool_calls": True}
    assert rec["confidence"] == "low"  # only 3 runs


def test_analyzer_finds_duplicates_and_respects_enabled_options(db):
    def dup_provider():
        return Scripted(
            resp(calls=[call(q="acme")]), resp(calls=[call(q="acme")]), resp(text="done")
        )

    v = _version(db, ["test_slow_a"])
    runs = []
    for _ in range(2):
        register_provider("mock", dup_provider)
        runs.append(execute_run(db, v, "x"))
    rec = {r["type"]: r for r in analyze(db, v, runs)}["dedupe_tools"]
    assert rec["evidence"]["duplicate_calls"] == 2
    assert rec["evidence"]["duplicate_share_pct"] == 50.0

    v_on = _version(db, ["test_slow_a"], {"dedupe_tool_calls": True})
    assert "dedupe_tools" not in {r["type"] for r in analyze(db, v_on, runs)}


def test_analyzer_estimates_caching_saving_from_prices(db):
    db.add(
        ModelPricing(
            provider="mock",
            model="mock-model",
            input_price_per_mtok=Decimal("5"),
            output_price_per_mtok=Decimal("25"),
            cache_write_price_per_mtok=Decimal("6.25"),
            cache_read_price_per_mtok=Decimal("0.5"),
            effective_date=__import__("datetime").date(2026, 1, 1),
        )
    )
    db.commit()
    v = _version(db, ["test_slow_a"], system="word " * 1500)  # long, cacheable prefix
    runs = [execute_run(db, v, "go") for _ in range(2)]
    rec = {r["type"]: r for r in analyze(db, v, runs)}["prompt_caching"]
    assert rec["estimated_impact"]["reusable_prefix_tokens_per_run"] >= 1500
    assert Decimal(rec["estimated_impact"]["estimated_saving_per_run"]) > 0


def test_analyzer_suggests_routing_with_repriced_cost_only(db):
    for model, price in (("mock-model", "10"), ("mock-small", "1")):
        db.add(
            ModelPricing(
                provider="mock",
                model=model,
                input_price_per_mtok=Decimal(price),
                output_price_per_mtok=Decimal(price),
                effective_date=__import__("datetime").date(2026, 1, 1),
            )
        )
    db.commit()
    v = _version(db)  # no tools: every run is a single call
    runs = [execute_run(db, v, "hello") for _ in range(3)]
    rec = {r["type"]: r for r in analyze(db, v, runs)}["model_routing"]
    assert rec["estimated_impact"]["cheaper_model"] == "mock-small"
    assert rec["estimated_impact"]["simple_runs_cost_reduction_pct_if_repriced"] == 90.0
    assert "unverified" in rec["basis"]
    assert rec["suggested_options"]["routing"]["mode"] == "classifier"


def test_analyzer_with_no_runs():
    assert analyze(None, AgentVersion(options={}), []) == []
