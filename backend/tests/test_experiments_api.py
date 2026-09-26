"""Integration tests: baseline vs candidate over the same dataset, end to end."""

from pathlib import Path

import pytest

from app.models import Run
from app.providers import register_provider
from app.providers.mock_provider import MockProvider

DATASET_FILE = Path(__file__).parent.parent / "datasets" / "company_research.jsonl"


@pytest.fixture(autouse=True)
def _mock_provider():
    register_provider("mock", MockProvider)
    yield


@pytest.fixture
def setup(client):
    """Agent with a baseline version (mock-model) and a cheaper candidate
    (mock-model-small at half the price, otherwise identical)."""
    for model, inp, out in [("mock-model", "1", "2"), ("mock-model-small", "0.5", "1")]:
        client.post(
            "/pricing/models",
            json={
                "provider": "mock",
                "model": model,
                "input_price_per_mtok": inp,
                "output_price_per_mtok": out,
                "effective_date": "2026-01-01",
            },
        )
    base_cfg = {"provider": "mock", "model": "mock-model", "tools": ["company_lookup"]}
    agent = client.post("/agents", json={"name": "Research", **base_cfg}).json()
    small = client.post(
        f"/agents/{agent['id']}/versions", json={**base_cfg, "model": "mock-model-small"}
    ).json()
    no_tools = client.post(f"/agents/{agent['id']}/versions", json={**base_cfg, "tools": []}).json()
    dataset = client.post(
        "/datasets/jsonl", params={"name": "company_research"}, content=DATASET_FILE.read_bytes()
    ).json()
    ev = client.post(
        "/evaluators",
        json={"name": "mentions", "type": "contains", "config": {"labels_key": "must_mention"}},
    ).json()
    return {
        "baseline": agent["latest_version"]["id"],
        "small": small["id"],
        "no_tools": no_tools["id"],
        "dataset": dataset["id"],
        "evaluator": ev["id"],
    }


CRITERIA = {
    "max_quality_drop_points": 2,
    "min_cost_reduction_pct": 20,
    "max_error_rate": 0.05,
    "min_pairs": 10,
}


def _create(client, s, candidate, criteria=CRITERIA, repetitions=2):
    r = client.post(
        "/experiments",
        json={
            "name": "cheaper model",
            "dataset_id": s["dataset"],
            "baseline_agent_version_id": s["baseline"],
            "candidate_agent_version_id": candidate,
            "evaluator_ids": [s["evaluator"]],
            "repetitions": repetitions,
            "acceptance_criteria": criteria,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _run(client, exp_or_id):
    exp_id = exp_or_id["id"] if isinstance(exp_or_id, dict) else exp_or_id
    r = client.post(f"/experiments/{exp_id}/run")
    assert r.status_code == 202, r.text
    return client.get(f"/experiments/{exp_id}").json()


def test_cheaper_model_with_same_quality_passes(client, db, setup):
    exp = _create(client, setup, setup["small"])
    assert exp["status"] == "created" and exp["verdict"] is None
    snap = exp["config_snapshot"]
    assert snap["baseline"]["model"] == "mock-model"
    assert snap["candidate"]["model"] == "mock-model-small"
    assert snap["dataset"] == {
        "id": setup["dataset"],
        "name": "company_research",
        "version": 1,
        "cases": 5,
    }
    assert snap["evaluators"][0]["config"]["labels_key"] == "must_mention"

    exp = _run(client, exp["id"])
    assert exp["status"] == "completed", exp["error"]
    assert exp["verdict"] == "PASS", exp["comparison"]["reasons"]
    c = exp["comparison"]
    assert c["pairs"] == 10  # 5 cases x 2 repetitions
    assert c["metrics"]["cost_per_run"]["change_pct"] == pytest.approx(-50)
    assert c["metrics"]["quality"]["change_points"] == 0

    # Both arms are ordinary evaluations tagged with the experiment and their arm.
    for arm, key in (
        ("baseline", "baseline_evaluation_id"),
        ("candidate", "candidate_evaluation_id"),
    ):
        ev = client.get(f"/evaluations/{exp[key]}").json()
        assert (ev["arm"], ev["experiment_id"], ev["status"]) == (arm, exp["id"], "completed")
        assert ev["summary"]["runs"] == 10 and ev["summary"]["cases"] == 5

    # Arms are interleaved and alternate which goes first.
    runs = db.query(Run).filter(Run.experiment_id == exp["id"]).order_by(Run.started_at).all()
    first = ["b" if r.agent_version_id == setup["baseline"] else "c" for r in runs[::2]]
    assert first[:4] == ["b", "c", "c", "b"]

    rows = client.get(f"/experiments/{exp['id']}/cases").json()
    assert len(rows) == 10
    assert all(r["baseline"] and r["candidate"] for r in rows)
    assert {r["quality_delta"] for r in rows} == {0}


def test_dropping_the_tool_fails_on_quality(client, setup):
    exp = _run(client, _create(client, setup, setup["no_tools"])["id"])
    assert exp["verdict"] == "FAIL"
    assert any("quality dropped" in r for r in exp["comparison"]["reasons"])
    rows = client.get(f"/experiments/{exp['id']}/cases").json()
    assert rows[0]["quality_delta"] < 0  # regressions listed first


def test_too_few_pairs_is_inconclusive(client, setup):
    exp = _run(client, _create(client, setup, setup["small"], repetitions=1))
    assert exp["verdict"] == "INCONCLUSIVE"
    assert "only 5 paired runs" in exp["comparison"]["reasons"][-1]


def test_run_once_then_reproduce(client, setup):
    first = _run(client, _create(client, setup, setup["small"])["id"])
    assert client.post(f"/experiments/{first['id']}/run").status_code == 409

    copy = client.post(f"/experiments/{first['id']}/reproduce").json()
    assert copy["reproduction_of"] == first["id"]
    assert copy["config_snapshot"] == first["config_snapshot"]
    again = _run(client, copy["id"])
    assert again["verdict"] == first["verdict"]
    for key in ("quality", "cost_per_run", "pass_rate"):
        assert again["comparison"]["metrics"][key] == first["comparison"]["metrics"][key]


def test_a_a_experiment_allowed(client, setup):
    exp = _run(
        client,
        _create(
            client,
            setup,
            setup["baseline"],
            criteria={"max_quality_drop_points": 1, "min_pairs": 10},
        ),
    )
    assert exp["verdict"] == "PASS"
    assert exp["comparison"]["metrics"]["cost_per_run"]["change_pct"] == 0


def test_validation(client, setup):
    def post(**kw):
        body = {
            "name": "x",
            "dataset_id": setup["dataset"],
            "baseline_agent_version_id": setup["baseline"],
            "candidate_agent_version_id": setup["small"],
            "evaluator_ids": [setup["evaluator"]],
            "acceptance_criteria": CRITERIA,
            **kw,
        }
        return client.post("/experiments", json=body).status_code

    assert post(acceptance_criteria={}) == 422
    assert post(acceptance_criteria={"max_error_rate": 2}) == 422
    assert post(candidate_agent_version_id="missing") == 404
    assert post(dataset_id="missing") == 404
    assert post(evaluator_ids=["missing"]) == 404
    assert post(repetitions=0) == 422
    assert client.get("/experiments/missing").status_code == 404
    assert client.post("/experiments/missing/run").status_code == 404
