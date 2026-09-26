import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.costs.calculator import MissingPriceError, ModelPrices, llm_cost
from app.costs.pricing import find_model_pricing
from app.costs.seed import seed_pricing
from app.engine.executor import execute_run
from app.models import Agent, AgentVersion, ModelPricing, ToolPricing
from app.providers import register_provider
from app.providers.base import LLMResponse, ToolCallRequest
from app.providers.mock_provider import MockProvider

OPUS = ModelPrices(Decimal("5"), Decimal("25"), Decimal("6.25"), Decimal("0.50"))
TODAY = datetime.now(UTC).date()


# --- calculator -----------------------------------------------------------------------


def test_llm_cost_is_exact_per_million_tokens():
    cost = llm_cost(OPUS, input_tokens=1234, output_tokens=567)
    assert cost.input == Decimal("0.00617")
    assert cost.output == Decimal("0.014175")
    assert cost.total == Decimal("0.020345")


def test_llm_cost_one_million_tokens_equals_list_price():
    assert llm_cost(OPUS, 1_000_000, 1_000_000).total == Decimal("30")


def test_llm_cost_prices_cache_tokens_separately():
    cost = llm_cost(OPUS, 100, 10, cache_write_tokens=2000, cache_read_tokens=10_000)
    assert cost.cache_write == Decimal("0.0125")
    assert cost.cache_read == Decimal("0.005")
    assert cost.total == Decimal("0.0005") + Decimal("0.00025") + Decimal("0.0175")


def test_missing_cache_price_only_matters_if_cache_tokens_used():
    no_cache = ModelPrices(Decimal("1"), Decimal("2"))
    assert llm_cost(no_cache, 10, 10).cache_read == 0
    with pytest.raises(MissingPriceError):
        llm_cost(no_cache, 10, 10, cache_read_tokens=5)


def test_negative_tokens_rejected():
    with pytest.raises(ValueError):
        llm_cost(OPUS, -1, 0)


# --- registry lookup ------------------------------------------------------------------


def _price(
    db, model="mock-model", provider="mock", eff=date(2026, 1, 1), inp="1", out="2", **kw
) -> ModelPricing:
    p = ModelPricing(
        provider=provider,
        model=model,
        input_price_per_mtok=Decimal(inp),
        output_price_per_mtok=Decimal(out),
        effective_date=eff,
        **kw,
    )
    db.add(p)
    db.commit()
    return p


def test_lookup_uses_latest_price_effective_on_date(db):
    old = _price(db, eff=date(2026, 1, 1), inp="1")
    new = _price(db, eff=date(2026, 6, 1), inp="3")
    assert find_model_pricing(db, "mock", "mock-model", date(2025, 12, 31)) is None
    assert find_model_pricing(db, "mock", "mock-model", date(2026, 3, 1)).id == old.id
    assert find_model_pricing(db, "mock", "mock-model", date(2026, 6, 1)).id == new.id
    assert find_model_pricing(db, "mock", "other-model", date(2026, 6, 1)) is None


def test_seed_is_idempotent(db):
    added = seed_pricing(db)
    assert added > 0
    assert seed_pricing(db) == 0
    opus = find_model_pricing(db, "anthropic", "claude-opus-5", date(2026, 9, 1))
    assert (opus.input_price_per_mtok, opus.output_price_per_mtok) == (5, 25)


# --- cost applied to runs --------------------------------------------------------------


def _version(db, tools=("company_lookup",)) -> AgentVersion:
    agent = Agent(name=f"agent-{uuid.uuid4()}")
    version = AgentVersion(
        version=1,
        provider="mock",
        model="mock-model",
        system_prompt="s",
        max_tokens=100,
        tools=list(tools),
    )
    agent.versions.append(version)
    db.add(agent)
    db.commit()
    return version


@pytest.fixture(autouse=True)
def _reset_mock_provider():
    yield
    register_provider("mock", MockProvider)


def test_run_cost_is_sum_of_priced_llm_calls(db):
    _price(db, inp="1", out="2")
    run = execute_run(db, _version(db), "Acme Corp")

    calls = [s.llm_call for s in run.steps if s.llm_call]
    expected = sum(
        (Decimal(c.input_tokens) / 10**6 * 1 + Decimal(c.output_tokens) / 10**6 * 2 for c in calls),
        start=Decimal(0),
    )
    assert run.cost_status == "complete"
    assert run.currency == "USD"
    assert run.estimated_llm_cost == expected > 0
    assert run.estimated_tool_cost == 0  # company_lookup has no price entry
    assert run.estimated_total_cost == expected
    for c in calls:
        assert c.estimated_cost == c.input_cost + c.output_cost
        assert Decimal(c.pricing_snapshot["input_price_per_mtok"]) == 1


def test_unpriced_model_leaves_totals_empty(db):
    run = execute_run(db, _version(db), "Acme Corp")
    assert run.status == "succeeded"
    assert run.cost_status == "unpriced"
    assert run.estimated_total_cost is None


def test_new_prices_do_not_change_past_runs(db):
    _price(db, eff=date(2026, 1, 1), inp="1", out="2")
    version = _version(db)
    first = execute_run(db, version, "Acme Corp")
    first_cost = first.estimated_total_cost

    _price(db, eff=TODAY, inp="10", out="20")  # price rise from today
    second = execute_run(db, version, "Acme Corp")

    db.expire_all()
    assert first.estimated_total_cost == first_cost  # re-read from DB, unchanged
    assert second.estimated_total_cost == first_cost * 10


def test_tool_price_added_to_run_cost(db):
    _price(db)
    db.add(
        ToolPricing(
            tool_name="company_lookup",
            price_per_call=Decimal("0.005"),
            effective_date=date(2026, 1, 1),
        )
    )
    db.commit()
    run = execute_run(db, _version(db), "Acme Corp")

    assert run.estimated_tool_cost == Decimal("0.005")
    assert run.estimated_total_cost == run.estimated_llm_cost + Decimal("0.005")
    assert run.steps[1].tool_call.estimated_cost == Decimal("0.005")


class _Scripted:
    name = "mock"

    def __init__(self, responses):
        self.responses = iter(responses)

    def complete(self, request):
        return next(self.responses)


def _resp(**kw) -> LLMResponse:
    base = dict(
        text="",
        tool_calls=[],
        stop_reason="end_turn",
        input_tokens=100,
        output_tokens=10,
        response_model="mock-model",
        raw_request={},
        raw_response={},
    )
    return LLMResponse(**{**base, **kw})


def test_cache_tokens_without_cache_price_make_cost_partial(db):
    _price(db)  # no cache prices
    register_provider(
        "mock",
        lambda: _Scripted(
            [
                _resp(
                    stop_reason="tool_use",
                    tool_calls=[ToolCallRequest("t1", "calculator", {"expression": "1+1"})],
                ),
                _resp(text="2", cache_read_input_tokens=500),
            ]
        ),
    )
    run = execute_run(db, _version(db, tools=("calculator",)), "1+1")

    assert run.status == "succeeded"
    assert run.cost_status == "partial"
    assert run.estimated_total_cost is None
    assert run.cache_read_input_tokens == 500
    first, second = run.steps[0].llm_call, run.steps[2].llm_call
    assert first.estimated_cost is not None and second.estimated_cost is None
    assert second.total_tokens == 100 + 10 + 500


def test_latency_totals_match_steps(db):
    run = execute_run(db, _version(db), "Acme Corp")
    llm = sum(s.latency_ms for s in run.steps if s.type == "llm_call")
    tool = sum(s.latency_ms for s in run.steps if s.type == "tool_call")
    assert run.llm_latency_ms == pytest.approx(llm)
    # Tool time is wall-clock per batch: >= the (sequential) step latencies it contains.
    assert run.tool_latency_ms >= tool
    assert run.llm_latency_ms + run.tool_latency_ms <= run.total_latency_ms
