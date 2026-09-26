"""Integration tests: dataset -> evaluation -> agent runs -> evaluators -> summary.

test_regression_suite doubles as the plan's evaluation regression suite: the fixed dataset
in datasets/company_research.jsonl must keep passing against the reference (mock) agent.
"""

from pathlib import Path

import pytest

from app.models import Run
from app.providers import register_provider
from app.providers.base import LLMResponse
from app.providers.mock_provider import MockProvider

DATASET_FILE = Path(__file__).parent.parent / "datasets" / "company_research.jsonl"

AGENT = {
    "name": "Company Research Agent",
    "provider": "mock",
    "model": "mock-model",
    "system_prompt": "Research the company.",
    "tools": ["company_lookup"],
}


@pytest.fixture(autouse=True)
def _mock_provider():
    register_provider("mock", MockProvider)
    yield
    register_provider("mock", MockProvider)


def _upload(client, name="company_research"):
    r = client.post(
        "/datasets/jsonl",
        params={"name": name},
        content=DATASET_FILE.read_bytes(),
        headers={"content-type": "application/x-ndjson"},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _evaluator(client, name, type_, config):
    r = client.post("/evaluators", json={"name": name, "type": type_, "config": config})
    assert r.status_code == 201, r.text
    return r.json()


def _evaluate(client, agent_id, dataset_id, evaluator_ids):
    r = client.post(
        "/evaluations",
        json={"agent_id": agent_id, "dataset_id": dataset_id, "evaluator_ids": evaluator_ids},
    )
    assert r.status_code == 202, r.text
    # TestClient runs background tasks before returning, so the evaluation is finished.
    return client.get(f"/evaluations/{r.json()['id']}").json()


def test_regression_suite(client, db):
    dataset = _upload(client)
    agent = client.post("/agents", json=AGENT).json()
    mentions = _evaluator(client, "mentions-industry", "contains", {"labels_key": "must_mention"})
    tools = _evaluator(
        client, "uses-lookup", "tool_calls", {"required": ["company_lookup"], "max_calls": 2}
    )
    judge = _evaluator(
        client,
        "relevance",
        "llm_judge",
        {"criterion": "relevance", "provider": "mock", "model": "mock-model"},
    )

    ev = _evaluate(client, agent["id"], dataset["id"], [mentions["id"], tools["id"], judge["id"]])

    assert ev["status"] == "completed", ev["error"]
    s = ev["summary"]
    assert s["cases"] == 5 and s["success_rate"] == 1.0
    assert s["quality"]["runs_scored"] == 5
    assert s["quality"]["pass_rate"] == 1.0  # regression gate
    assert s["evaluators"][mentions["id"]]["pass_rate"] == 1.0
    assert s["evaluators"][tools["id"]]["score"]["mean"] == 1.0
    assert s["evaluators"][judge["id"]]["score"]["mean"] == 0.75  # mock judge always says 4
    assert s["judge_calls"] == 5

    results = client.get(f"/evaluations/{ev['id']}/results").json()
    assert len(results) == 15
    assert [r["case_key"] for r in results[::3]] == [
        "acme",
        "globex",
        "initech",
        "umbrella",
        "unknown",
    ]
    judged = [r for r in results if r["evaluator_name"] == "relevance"]
    assert all(r["judge_prompt_version"] == "judge-v1" for r in judged)
    assert all("MOCK JUDGE" in r["reason"] for r in judged)

    # Every run is traceable back to its evaluation and dataset case.
    runs = db.query(Run).filter(Run.evaluation_id == ev["id"]).all()
    assert len(runs) == 5 and all(r.dataset_case_id for r in runs)


class _BrokenJudge:
    name = "broken"

    def complete(self, request):
        return LLMResponse(
            text="I think it's good",
            tool_calls=[],
            stop_reason="end_turn",
            input_tokens=10,
            output_tokens=5,
            response_model=request.model,
            raw_request={},
            raw_response={},
        )


def test_evaluator_errors_do_not_abort_or_count_as_zero(client):
    register_provider("broken", _BrokenJudge)
    try:
        dataset = _upload(client)
        agent = client.post("/agents", json=AGENT).json()
        good = _evaluator(client, "mentions", "contains", {"labels_key": "must_mention"})
        bad = _evaluator(
            client,
            "broken-judge",
            "llm_judge",
            {"criterion": "relevance", "provider": "broken", "model": "x"},
        )
        ev = _evaluate(client, agent["id"], dataset["id"], [good["id"], bad["id"]])
    finally:
        register_provider("broken", MockProvider)

    assert ev["status"] == "completed"
    s = ev["summary"]
    assert s["evaluators"][bad["id"]]["errors"] == 5
    assert s["evaluators"][bad["id"]]["scored"] == 0
    assert s["quality"]["score"]["mean"] == 1.0  # errors excluded, not averaged in as 0


def test_judge_cost_tracked_separately_from_agent_cost(client):
    client.post(
        "/pricing/models",
        json={
            "provider": "mock",
            "model": "mock-model",
            "input_price_per_mtok": "1",
            "output_price_per_mtok": "2",
            "effective_date": "2026-01-01",
        },
    )
    dataset = _upload(client)
    agent = client.post("/agents", json=AGENT).json()
    judge = _evaluator(
        client,
        "relevance",
        "llm_judge",
        {"criterion": "relevance", "provider": "mock", "model": "mock-model"},
    )
    s = _evaluate(client, agent["id"], dataset["id"], [judge["id"]])["summary"]
    assert float(s["judge_estimated_cost"]) > 0
    assert float(s["estimated_total_cost"]) > 0
    assert s["runs_without_cost"] == 0


def test_dataset_versioning_and_validation(client):
    first, second = _upload(client), _upload(client)
    assert (first["version"], second["version"]) == (1, 2)
    assert first["case_count"] == 5

    detail = client.get(f"/datasets/{first['id']}").json()
    assert detail["cases"][0]["case_key"] == "acme"
    assert detail["cases"][0]["labels"] == {"must_mention": ["Industrial Automation"]}

    r = client.post("/datasets", json={"name": "d", "cases": [{"input": "a"}, {"input": "b"}]})
    assert [c["case_key"] for c in r.json()["cases"]] == ["case_001", "case_002"]
    dup = client.post(
        "/datasets",
        json={"name": "d", "cases": [{"id": "x", "input": "a"}, {"id": "x", "input": "b"}]},
    )
    assert dup.status_code == 422
    assert client.post("/datasets", json={"name": "d", "cases": []}).status_code == 422

    bad = client.post(
        "/datasets/jsonl",
        params={"name": "d"},
        content=b'{"input": "ok"}\nnot json\n{"no_input": 1}\n',
    )
    assert bad.status_code == 422
    assert [e.split(":")[0] for e in bad.json()["detail"]] == ["line 2", "line 3"]
    assert client.get("/datasets/missing").status_code == 404


def test_evaluator_versioning_and_validation(client):
    body = {"name": "json", "type": "json_schema", "config": {"json_schema": {"type": "object"}}}
    v1, v2 = client.post("/evaluators", json=body).json(), client.post("/evaluators", json=body)
    assert (v1["version"], v2.json()["version"]) == (1, 2)
    judge = client.post(
        "/evaluators",
        json={
            "name": "j",
            "type": "llm_judge",
            "config": {"criterion": "relevance", "provider": "mock", "model": "m"},
        },
    ).json()
    assert judge["config"]["pass_threshold"] == 0.75  # defaults stored on the version

    bad_type = client.post("/evaluators", json={**body, "type": "vibes"})
    bad_cfg = client.post("/evaluators", json={**body, "config": {"json_schema": {"type": 1}}})
    bad_provider = client.post(
        "/evaluators",
        json={
            "name": "j",
            "type": "llm_judge",
            "config": {"criterion": "relevance", "provider": "nope", "model": "m"},
        },
    )
    assert {bad_type.status_code, bad_cfg.status_code, bad_provider.status_code} == {422}


def test_evaluation_request_validation(client):
    dataset = _upload(client)
    agent = client.post("/agents", json=AGENT).json()
    ev = _evaluator(client, "m", "contains", {"values": ["x"]})

    def post(**kw):
        body = {
            "agent_id": agent["id"],
            "dataset_id": dataset["id"],
            "evaluator_ids": [ev["id"]],
            **kw,
        }
        return client.post("/evaluations", json=body).status_code

    assert post(dataset_id="missing") == 404
    assert post(evaluator_ids=["missing"]) == 404
    assert post(evaluator_ids=[ev["id"], ev["id"]]) == 422
    assert post(agent_id="missing") == 404
    assert post(agent_version_id="missing") == 404
    assert client.get("/evaluations/missing").status_code == 404
