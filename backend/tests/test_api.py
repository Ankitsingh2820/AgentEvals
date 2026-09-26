"""API integration tests: API -> agent -> (mock) LLM -> trace -> database."""

from decimal import Decimal

import pytest

from app.providers import register_provider
from app.providers.mock_provider import MockProvider

AGENT = {
    "name": "Company Research Agent",
    "description": "Researches companies",
    "provider": "mock",
    "model": "mock-model",
    "system_prompt": "You research companies.",
    "temperature": 0,
    "tools": ["company_lookup"],
    "tags": ["demo"],
}


@pytest.fixture(autouse=True)
def _mock_provider():
    register_provider(
        "mock", lambda: MockProvider(tool_arguments={"company_lookup": {"name": "Acme Corp"}})
    )
    yield
    register_provider("mock", MockProvider)


def test_meta_endpoints(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert {"anthropic", "mock"} <= set(client.get("/providers").json())
    assert "company_lookup" in [t["name"] for t in client.get("/tools").json()]


def test_create_and_get_agent(client):
    r = client.post("/agents", json=AGENT)
    assert r.status_code == 201
    agent = r.json()
    assert agent["latest_version"]["version"] == 1
    assert agent["latest_version"]["tools"] == ["company_lookup"]

    assert client.get(f"/agents/{agent['id']}").json()["name"] == AGENT["name"]
    assert len(client.get("/agents").json()) == 1
    assert client.post("/agents", json=AGENT).status_code == 409


@pytest.mark.parametrize(
    "override", [{"provider": "nope"}, {"tools": ["nope"]}, {"temperature": 5}, {"max_tokens": 0}]
)
def test_invalid_agent_config_rejected(client, override):
    assert client.post("/agents", json={**AGENT, **override}).status_code == 422


def test_new_version_does_not_alter_old_runs(client):
    agent = client.post("/agents", json=AGENT).json()
    run_v1 = client.post("/runs", json={"agent_id": agent["id"], "input": "Research Acme"}).json()

    r = client.post(f"/agents/{agent['id']}/versions", json={**AGENT, "tools": []})
    assert r.status_code == 201 and r.json()["version"] == 2
    assert [v["version"] for v in client.get(f"/agents/{agent['id']}/versions").json()] == [1, 2]

    run_v2 = client.post("/runs", json={"agent_id": agent["id"], "input": "Hi"}).json()
    assert run_v2["agent_version_id"] == r.json()["id"]
    assert run_v2["tool_call_count"] == 0

    trace_v1 = client.get(f"/runs/{run_v1['id']}/trace").json()
    assert trace_v1["agent_version"]["version"] == 1
    assert trace_v1["agent_version"]["tools"] == ["company_lookup"]

    # A run can pin an older version explicitly.
    pinned = client.post(
        "/runs",
        json={
            "agent_id": agent["id"],
            "input": "Research Acme",
            "agent_version_id": run_v1["agent_version_id"],
        },
    ).json()
    assert pinned["tool_call_count"] == 1


def test_run_and_trace(client):
    agent = client.post("/agents", json=AGENT).json()
    r = client.post("/runs", json={"agent_id": agent["id"], "input": "Research Acme Corp"})
    assert r.status_code == 201
    run = r.json()
    assert run["status"] == "succeeded"
    assert run["llm_call_count"] == 2 and run["tool_call_count"] == 1

    trace = client.get(f"/runs/{run['id']}/trace").json()
    assert [s["type"] for s in trace["steps"]] == ["llm_call", "tool_call", "llm_call"]
    assert trace["steps"][1]["tool_call"]["arguments"] == {"name": "Acme Corp"}
    assert trace["steps"][0]["llm_call"]["model"] == "mock-model"
    lat = trace["latency"]
    assert lat["llm_ms"] + lat["tool_ms"] + lat["other_ms"] == pytest.approx(lat["total_ms"])

    assert client.get(f"/runs/{run['id']}").json()["id"] == run["id"]
    assert len(client.get("/runs", params={"agent_id": agent["id"]}).json()) == 1


def test_not_found(client):
    assert client.get("/agents/missing").status_code == 404
    assert client.get("/runs/missing").status_code == 404
    assert client.post("/runs", json={"agent_id": "missing", "input": "x"}).status_code == 404
    agent = client.post("/agents", json=AGENT).json()
    r = client.post(
        "/runs", json={"agent_id": agent["id"], "input": "x", "agent_version_id": "missing"}
    )
    assert r.status_code == 404


MOCK_PRICE = {
    "provider": "mock",
    "model": "mock-model",
    "input_price_per_mtok": "1",
    "output_price_per_mtok": "2",
    "effective_date": "2026-01-01",
}


def test_pricing_registry_is_append_only(client):
    r = client.post("/pricing/models", json=MOCK_PRICE)
    assert r.status_code == 201
    assert Decimal(r.json()["input_price_per_mtok"]) == 1
    assert client.post("/pricing/models", json=MOCK_PRICE).status_code == 409

    newer = {**MOCK_PRICE, "input_price_per_mtok": "3", "effective_date": "2026-06-01"}
    assert client.post("/pricing/models", json=newer).status_code == 201
    history = client.get("/pricing/models", params={"model": "mock-model"}).json()
    assert [p["effective_date"] for p in history] == ["2026-06-01", "2026-01-01"]

    current = client.get(
        "/pricing/models/current",
        params={"provider": "mock", "model": "mock-model", "on": "2026-03-01"},
    )
    assert Decimal(current.json()["input_price_per_mtok"]) == 1
    missing = client.get("/pricing/models/current", params={"provider": "x", "model": "y"})
    assert missing.status_code == 404

    assert client.put("/pricing/models", json=MOCK_PRICE).status_code == 405


@pytest.mark.parametrize(
    "override", [{"input_price_per_mtok": "-1"}, {"currency": "usd"}, {"effective_date": "x"}]
)
def test_invalid_pricing_rejected(client, override):
    assert client.post("/pricing/models", json={**MOCK_PRICE, **override}).status_code == 422


def test_tool_pricing(client):
    body = {
        "tool_name": "company_lookup",
        "price_per_call": "0.005",
        "effective_date": "2026-01-01",
    }
    assert client.post("/pricing/tools", json=body).status_code == 201
    assert client.post("/pricing/tools", json=body).status_code == 409
    assert Decimal(client.get("/pricing/tools").json()[0]["price_per_call"]) == Decimal("0.005")


def test_run_reports_estimated_cost(client):
    client.post("/pricing/models", json=MOCK_PRICE)
    agent = client.post("/agents", json=AGENT).json()
    run = client.post("/runs", json={"agent_id": agent["id"], "input": "Acme"}).json()

    assert run["cost_status"] == "complete"
    assert run["currency"] == "USD"
    trace = client.get(f"/runs/{run['id']}/trace").json()
    step_costs = [s["llm_call"]["estimated_cost"] for s in trace["steps"] if s["llm_call"]]
    assert sum(Decimal(c) for c in step_costs) == Decimal(run["estimated_total_cost"])


def test_agent_metrics(client):
    client.post("/pricing/models", json=MOCK_PRICE)
    agent = client.post("/agents", json=AGENT).json()
    for _ in range(3):
        client.post("/runs", json={"agent_id": agent["id"], "input": "Acme"})

    m = client.get(f"/metrics/agents/{agent['id']}").json()
    assert m["runs"] == 3 and m["succeeded"] == 3 and m["success_rate"] == 1.0
    assert m["latency_ms"]["count"] == 3
    assert m["latency_ms"]["p95"] >= m["latency_ms"]["median"]
    assert m["estimated_cost"]["count"] == 3
    assert m["runs_without_cost"] == 0
    assert m["llm_calls"]["mean"] == 2 and m["tool_calls"]["mean"] == 1
    assert m["tool_failure_rate"] == 0

    empty = client.get(f"/metrics/agents/{agent['id']}", params={"agent_version_id": "none"})
    assert empty.json()["runs"] == 0 and empty.json()["success_rate"] is None
    assert client.get("/metrics/agents/missing").status_code == 404


def test_overview_kpis_and_trend(client):
    client.post("/pricing/models", json=MOCK_PRICE)
    agent = client.post("/agents", json=AGENT).json()
    for _ in range(3):
        client.post("/runs", json={"agent_id": agent["id"], "input": "Acme"})

    o = client.get("/metrics/overview", params={"days": 7}).json()
    k = o["kpis"]
    assert k["runs"] == 3 and k["success_rate"] == 1.0
    assert k["runs_with_cost"] == 3 and k["avg_cost"] > 0
    assert k["p95_latency_ms"] >= k["p50_latency_ms"]
    assert k["avg_quality"] is None and k["runs_with_quality"] == 0  # nothing evaluated
    assert o["previous"]["runs"] == 0
    assert len(o["trend"]) == 8  # today + 7 previous days, gaps filled
    assert o["trend"][-1]["runs"] == 3 and o["trend"][0]["runs"] == 0

    scoped = client.get("/metrics/overview", params={"agent_id": agent["id"]}).json()
    assert scoped["kpis"]["runs"] == 3
    assert client.get("/metrics/overview", params={"agent_id": "nope"}).status_code == 404
