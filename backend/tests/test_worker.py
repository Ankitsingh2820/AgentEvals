"""Task dispatch and at-least-once safety: re-delivered tasks skip or resume."""

from pathlib import Path

import pytest

from app.config import get_settings
from app.engine.executor import execute_run
from app.evaluation.runner import evaluate_case, ordered_evaluators, run_evaluation
from app.experiments.runner import run_experiment
from app.models import AgentVersion, Dataset, Evaluation, Experiment, Run
from app.providers import register_provider
from app.providers.mock_provider import MockProvider

DATASET_FILE = Path(__file__).parent.parent / "datasets" / "company_research.jsonl"


@pytest.fixture(autouse=True)
def _mock():
    register_provider("mock", MockProvider)


@pytest.fixture
def world(client):
    agent = client.post(
        "/agents",
        json={"name": "w", "provider": "mock", "model": "mock-model", "tools": ["company_lookup"]},
    ).json()
    dataset = client.post(
        "/datasets/jsonl", params={"name": "cr"}, content=DATASET_FILE.read_bytes()
    ).json()
    ev = client.post(
        "/evaluators",
        json={"name": "m", "type": "contains", "config": {"labels_key": "must_mention"}},
    ).json()
    return agent, dataset, ev


def test_celery_backend_enqueues_instead_of_running_inline(client, world, monkeypatch):
    agent, dataset, ev = world
    sent = []
    from app.worker import celery_app

    monkeypatch.setattr(get_settings(), "task_backend", "celery")
    monkeypatch.setattr(celery_app, "send_task", lambda name, args: sent.append((name, args)))
    r = client.post(
        "/evaluations",
        json={"agent_id": agent["id"], "dataset_id": dataset["id"], "evaluator_ids": [ev["id"]]},
    )
    assert r.status_code == 202
    assert sent == [("agenteval.run_evaluation", [r.json()["id"]])]
    assert client.get(f"/evaluations/{r.json()['id']}").json()["status"] == "pending"


def test_redelivered_finished_evaluation_is_a_no_op(client, db, session_factory, world):
    agent, dataset, ev = world
    eid = client.post(
        "/evaluations",
        json={"agent_id": agent["id"], "dataset_id": dataset["id"], "evaluator_ids": [ev["id"]]},
    ).json()["id"]
    before = db.query(Run).count()
    run_evaluation(session_factory, eid)  # second delivery
    assert db.query(Run).count() == before


def test_interrupted_evaluation_resumes_without_repeating_work(db, session_factory, world):
    agent, dataset, ev = world
    version = db.get(AgentVersion, agent["latest_version"]["id"])
    evaluation = Evaluation(
        agent_id=agent["id"],
        agent_version_id=version.id,
        dataset_id=dataset["id"],
        evaluator_ids=[ev["id"]],
        status="running",
    )
    db.add(evaluation)
    db.commit()
    cases = db.get(Dataset, dataset["id"]).cases
    evaluators = ordered_evaluators(db, [ev["id"]])
    # Case 1 finished before the crash; case 2 was cut off mid-run.
    finished, _ = evaluate_case(db, evaluation, version, cases[0], evaluators)
    cut_off = execute_run(
        db, version, cases[1].input, evaluation_id=evaluation.id, dataset_case_id=cases[1].id
    )
    cut_off.status = "running"
    db.commit()

    run_evaluation(session_factory, evaluation.id)  # redelivery after the crash

    db.expire_all()
    evaluation = db.get(Evaluation, evaluation.id)
    assert evaluation.status == "completed"
    linked = db.query(Run).filter(Run.evaluation_id == evaluation.id).all()
    assert len(linked) == 5
    assert finished.id in {r.id for r in linked}  # reused, not re-run
    orphan = db.get(Run, cut_off.id)
    assert orphan.evaluation_id is None and orphan.status == "failed"
    assert "interrupted" in orphan.error
    assert evaluation.summary["runs"] == 5


def test_redelivered_experiment_resumes_same_arms(client, db, session_factory, world):
    agent, dataset, ev = world
    v1 = agent["latest_version"]["id"]
    exp = client.post(
        "/experiments",
        json={
            "name": "e",
            "dataset_id": dataset["id"],
            "baseline_agent_version_id": v1,
            "candidate_agent_version_id": v1,
            "evaluator_ids": [ev["id"]],
            "acceptance_criteria": {"max_error_rate": 0.5, "min_pairs": 1},
        },
    ).json()
    client.post(f"/experiments/{exp['id']}/run")
    done = db.get(Experiment, exp["id"])
    arms = (done.baseline_evaluation_id, done.candidate_evaluation_id)
    runs_before = db.query(Run).count()

    # Simulate a crash after the work was done but before the task was acknowledged.
    for eid in arms:
        db.get(Evaluation, eid).status = "running"
    done.status = "running"
    db.commit()
    run_experiment(session_factory, exp["id"])

    db.expire_all()
    again = db.get(Experiment, exp["id"])
    assert again.status == "completed"
    assert (again.baseline_evaluation_id, again.candidate_evaluation_id) == arms
    assert db.query(Run).count() == runs_before  # nothing re-executed
    assert again.comparison["pairs"] == 5


# --- leases ------------------------------------------------------------------------------


def _pending_evaluation(client, db, world):
    agent, dataset, ev = world
    evaluation = Evaluation(
        agent_id=agent["id"],
        agent_version_id=agent["latest_version"]["id"],
        dataset_id=dataset["id"],
        evaluator_ids=[ev["id"]],
    )
    db.add(evaluation)
    db.commit()
    return evaluation


def test_lease_claim_renew_release(client, db, world):
    from datetime import UTC, datetime, timedelta

    from app.leases import claim, release, renew

    e = _pending_evaluation(client, db, world)
    assert claim(db, Evaluation, e.id, "A", 60) is True
    assert claim(db, Evaluation, e.id, "B", 60) is False  # live lease held by A
    assert claim(db, Evaluation, e.id, "A", 60) is True  # the owner may re-claim
    renew(db, Evaluation, e.id, "B", 60)  # a non-owner cannot renew
    db.refresh(e)
    assert e.lease_owner == "A"

    e.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)  # A stopped heartbeating
    db.commit()
    assert claim(db, Evaluation, e.id, "B", 60) is True  # expired: B takes over
    release(db, Evaluation, e.id, "A")  # stale owner's release is ignored
    db.refresh(e)
    assert e.lease_owner == "B"
    release(db, Evaluation, e.id, "B")
    db.refresh(e)
    assert e.lease_owner is None and e.lease_expires_at is None


def test_duplicate_delivery_backs_off_while_lease_is_live(client, db, session_factory, world):
    from app.leases import LeaseHeld, claim

    e = _pending_evaluation(client, db, world)
    e.status = "running"
    db.commit()
    assert claim(db, Evaluation, e.id, "original-worker", 300)

    with pytest.raises(LeaseHeld) as held:
        run_evaluation(session_factory, e.id, owner="duplicate")
    assert 250 < held.value.retry_in <= 300
    assert db.query(Run).filter(Run.evaluation_id == e.id).count() == 0  # nothing ran twice


def test_expired_lease_is_taken_over_and_released(client, db, session_factory, world):
    from datetime import UTC, datetime, timedelta

    e = _pending_evaluation(client, db, world)
    e.status, e.lease_owner = "running", "dead-worker"
    e.lease_expires_at = datetime.now(UTC) - timedelta(seconds=5)
    db.commit()

    run_evaluation(session_factory, e.id, owner="new-worker")
    db.expire_all()
    e = db.get(Evaluation, e.id)
    assert e.status == "completed"
    assert e.lease_owner is None  # released when done


def test_worker_requeues_a_task_whose_lease_is_held(
    client, db, session_factory, world, monkeypatch
):
    from celery.exceptions import Retry

    from app import db as db_module
    from app.leases import claim
    from app.worker import run_evaluation_task

    e = _pending_evaluation(client, db, world)
    e.status = "running"
    db.commit()
    claim(db, Evaluation, e.id, "someone-else", 120)
    monkeypatch.setattr(db_module, "SessionLocal", session_factory)
    captured = {}
    real_retry = run_evaluation_task.retry

    def spy_retry(*args, **kwargs):
        captured.update(kwargs)
        return real_retry(*args, **kwargs)

    monkeypatch.setattr(run_evaluation_task, "retry", spy_retry)
    with pytest.raises(Retry):
        run_evaluation_task(e.id)
    assert 100 < captured["countdown"] <= 121
    assert captured["max_retries"] is None


def test_recovery_finds_only_abandoned_top_level_work(client, db, session_factory, world):
    from datetime import UTC, datetime, timedelta

    from app.recovery import find_abandoned, requeue_abandoned

    agent, dataset, ev = world
    v1 = agent["latest_version"]["id"]
    past, future = (
        datetime.now(UTC) - timedelta(seconds=5),
        datetime.now(UTC) + timedelta(minutes=5),
    )

    def evaluation(status, lease_until=None, experiment_id=None):
        e = Evaluation(
            agent_id=agent["id"],
            agent_version_id=v1,
            dataset_id=dataset["id"],
            evaluator_ids=[ev["id"]],
            status=status,
            lease_expires_at=lease_until,
            experiment_id=experiment_id,
        )
        db.add(e)
        db.commit()
        return e.id

    dead = evaluation("running", past)  # worker died: lease lapsed
    unleased = evaluation("running", None)  # e.g. inline run lost with an API restart
    evaluation("running", future)  # alive: lease being renewed
    evaluation("completed", past)
    evaluation("pending", None)  # never started: its message is still queued

    exp = client.post(
        "/experiments",
        json={
            "name": "x",
            "dataset_id": dataset["id"],
            "baseline_agent_version_id": v1,
            "candidate_agent_version_id": v1,
            "evaluator_ids": [ev["id"]],
            "acceptance_criteria": {"max_error_rate": 1},
        },
    ).json()
    stuck = db.get(Experiment, exp["id"])
    stuck.status, stuck.lease_expires_at = "running", past
    db.commit()
    evaluation("running", None, experiment_id=exp["id"])  # an arm: driven by its experiment

    found = find_abandoned(db)
    assert sorted(found) == sorted(
        [("evaluation", dead), ("evaluation", unleased), ("experiment", exp["id"])]
    )
    sent = []
    assert requeue_abandoned(db, lambda name, args: sent.append((name, args))) == 3
    assert ("agenteval.run_experiment", [exp["id"]]) in sent
