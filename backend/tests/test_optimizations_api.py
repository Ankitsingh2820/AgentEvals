"""The optimization loop over HTTP: analyze -> apply -> experiment -> verdict."""

from pathlib import Path

import pytest

import tests.test_optimizations  # noqa: F401  (registers the slow test tools)
from app.providers import register_provider
from app.providers.mock_provider import MockProvider

DATASET_FILE = Path(__file__).parent.parent / "datasets" / "company_research.jsonl"
TOOLS3 = ["test_slow_a", "test_slow_b", "test_slow_c"]


@pytest.fixture(autouse=True)
def _mock():
    register_provider("mock", lambda: MockProvider(calls_per_turn=3))
    yield
    register_provider("mock", MockProvider)


@pytest.fixture
def world(client):
    agent = client.post(
        "/agents",
        json={"name": "Parallelizable", "provider": "mock", "model": "mock-model", "tools": TOOLS3},
    ).json()
    dataset = client.post(
        "/datasets/jsonl", params={"name": "cr"}, content=DATASET_FILE.read_bytes()
    ).json()
    evaluator = client.post(
        "/evaluators",
        json={
            "name": "uses-tools",
            "type": "tool_calls",
            "config": {"required": TOOLS3, "require_success": True},
        },
    ).json()
    for i in range(3):
        client.post("/runs", json={"agent_id": agent["id"], "input": f"company {i}"})
    return agent, dataset, evaluator


def test_analyze_apply_and_experiment_passes(client, world):
    agent, dataset, evaluator = world
    report = client.post("/optimizations/analyze", json={"agent_id": agent["id"]}).json()
    assert report["runs_analyzed"] == 3
    types = [r["type"] for r in report["recommendations"]]
    assert "parallel_tools" in types
    assert client.get(f"/optimizations/{report['id']}").json()["id"] == report["id"]
    assert len(client.get("/optimizations", params={"agent_id": agent["id"]}).json()) == 1

    r = client.post(
        f"/optimizations/{report['id']}/apply",
        json={
            "type": "parallel_tools",
            "experiment": {
                "dataset_id": dataset["id"],
                "evaluator_ids": [evaluator["id"]],
                "repetitions": 2,
                "acceptance_criteria": {
                    "max_quality_drop_points": 1,
                    "min_latency_reduction_pct": 30,
                    "min_pairs": 10,
                },
            },
        },
    )
    assert r.status_code == 201, r.text
    out = r.json()
    version = out["agent_version"]
    assert version["version"] == 2
    assert version["options"]["parallel_tool_calls"] is True
    assert version["metadata"]["optimization"]["type"] == "parallel_tools"
    exp = out["experiment"]
    assert exp["config_snapshot"]["config_diff"] == ["options"]
    assert exp["config_snapshot"]["isolate"] == ["options"]

    client.post(f"/experiments/{exp['id']}/run")
    done = client.get(f"/experiments/{exp['id']}").json()
    assert done["verdict"] == "PASS", done["comparison"]["reasons"]
    lat = done["comparison"]["metrics"]["latency_ms"]
    assert lat["change_pct"]["mean"] < -30
    assert done["comparison"]["metrics"]["quality"]["change_points"] == 0


def test_apply_errors(client, world):
    agent, *_ = world
    report = client.post("/optimizations/analyze", json={"agent_id": agent["id"]}).json()
    rid = report["id"]
    assert client.post(f"/optimizations/{rid}/apply", json={"type": "nope"}).status_code == 404
    bad = client.post(
        f"/optimizations/{rid}/apply",
        json={"type": "parallel_tools", "options_override": {"max_tool_calls": -5}},
    )
    assert bad.status_code == 422
    assert client.get("/optimizations/missing").status_code == 404
    assert (
        client.post(
            "/optimizations/analyze", json={"agent_id": agent["id"], "agent_version_id": "x"}
        ).status_code
        == 404
    )


def test_isolated_prompt_experiment(client, world):
    agent, dataset, evaluator = world
    base = agent["latest_version"]
    cfg = {"provider": "mock", "model": "mock-model", "tools": TOOLS3}
    prompt_b = client.post(
        f"/agents/{agent['id']}/versions", json={**cfg, "system_prompt": "Be brief."}
    ).json()
    model_b = client.post(
        f"/agents/{agent['id']}/versions",
        json={**cfg, "model": "mock-small", "system_prompt": "Be brief."},
    ).json()

    def create(candidate):
        return client.post(
            "/experiments",
            json={
                "name": "prompt A vs B",
                "dataset_id": dataset["id"],
                "baseline_agent_version_id": base["id"],
                "candidate_agent_version_id": candidate,
                "evaluator_ids": [evaluator["id"]],
                "isolate": ["system_prompt"],
                "acceptance_criteria": {"max_quality_drop_points": 1},
            },
        )

    ok = create(prompt_b["id"])
    assert ok.status_code == 201
    assert ok.json()["config_snapshot"]["config_diff"] == ["system_prompt"]
    confounded = create(model_b["id"])
    assert confounded.status_code == 422
    assert "model" in confounded.json()["detail"]
