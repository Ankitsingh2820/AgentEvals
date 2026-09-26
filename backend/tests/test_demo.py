"""The demo loader: a fresh install gets explorable data with one command."""

from app.demo import DEMO_AGENT, load_demo
from app.models import Agent, Evaluation, Experiment, Run


def test_demo_loads_once_and_everything_completes(db, session_factory):
    result = load_demo(db, session_factory)
    assert result["loaded"] is True
    assert result["experiment_verdict"] == "PASS"

    db.expire_all()
    assert {a.name for a in db.query(Agent)} == {DEMO_AGENT, "Long-prompt Agent (demo)"}
    assert db.get(Evaluation, result["evaluation_id"]).status == "completed"
    assert db.get(Experiment, result["experiment_id"]).status == "completed"
    runs = db.query(Run).count()

    again = load_demo(db, session_factory)  # idempotent
    assert again == {"loaded": False, "reason": "demo data already present"}
    assert db.query(Run).count() == runs
