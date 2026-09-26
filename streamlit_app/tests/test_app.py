"""Streamlit demo tests: the real script, driven headlessly with Streamlit's AppTest.
Mock model only: no network, no keys."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parent.parent / "app.py")


@pytest.fixture
def at():
    app = AppTest.from_file(APP, default_timeout=60)
    app.run()
    assert not app.exception, app.exception
    return app


def button(at, label):
    return next(b for b in at.button if b.label == label)


def test_loads_with_private_demo_workspace(at):
    labels = {m.label: m.value for m in at.metric}
    assert labels["Agents"] == "2"
    assert int(labels["Runs"]) >= 8
    assert any("PASS" in s.value for s in at.success)  # latest experiment verdict
    assert at.session_state.ws is not None


def test_run_an_agent_shows_answer_and_steps(at):
    button(at, "Run agent").click().run()
    assert not at.exception, at.exception
    assert any("Succeeded" in s.value for s in at.success)
    run_id = at.session_state.last_run
    assert run_id
    run = at.session_state.ws.db.get(__import__("engine").models.Run, run_id)
    assert run.status == "succeeded" and run.tool_call_count == 1


def test_evaluation_runs_and_reports_quality(at):
    button(at, "Run evaluation").click().run()
    assert not at.exception, at.exception
    labels = {m.label: m.value for m in at.metric}
    assert labels["Quality (mean score)"] == "100.0%"
    assert labels["Pass rate"] == "100.0%"


def test_experiment_gives_a_verdict(at):
    button(at, "Run experiment").click().run()
    assert not at.exception, at.exception
    assert at.session_state.last_exp
    exp = at.session_state.ws.db.get(
        __import__("engine").models.Experiment, at.session_state.last_exp
    )
    assert exp.status == "completed" and exp.verdict in ("PASS", "FAIL", "INCONCLUSIVE")


def test_real_provider_requires_the_visitors_own_key(at):
    # Connecting with an empty key is refused; nothing is connected.
    button(at, "Connect").click().run()
    assert any("Paste an API key" in e.value for e in at.error)
    assert at.session_state.ws.providers == {}


def test_workspaces_are_isolated_between_visitors():
    import engine as ae

    a, b = ae.new_workspace(), ae.new_workspace()
    v = ae.agents(a)[0].latest_version
    ae.run_agent(a, v.id, "Acme Corp")
    assert len(ae.runs(a)) == len(ae.runs(b)) + 1  # b never sees a's run
    a.providers["groq"] = object()  # a's key-bearing provider...
    assert "groq" not in b.providers  # ...is not b's


def test_host_keys_are_never_used(monkeypatch):
    import engine as ae

    assert ae.get_settings().openai_api_key is None
    assert ae.get_settings().groq_api_key is None
    import os

    assert not any(
        os.environ.get(k) for k in ("OPENAI_API_KEY", "GROQ_API_KEY", "ANTHROPIC_API_KEY")
    )
