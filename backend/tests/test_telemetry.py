"""OpenTelemetry spans mirror the run trace, with GenAI attributes and no content."""

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.telemetry import setup_telemetry

exporter = InMemorySpanExporter()


@pytest.fixture(scope="module", autouse=True)
def _tracing():
    # The global tracer provider can be set once per process; this module owns it.
    setup_telemetry(span_exporter=exporter)


def test_run_emits_nested_genai_spans(client):
    exporter.clear()
    agent = client.post(
        "/agents",
        json={
            "name": "otel",
            "provider": "mock",
            "model": "mock-model",
            "tools": ["company_lookup"],
        },
    ).json()
    run = client.post("/runs", json={"agent_id": agent["id"], "input": "Acme Corp"}).json()

    spans = {s.name: s for s in exporter.get_finished_spans()}
    root = spans["agent.run"]
    assert root.attributes["agenteval.run.id"] == run["id"]
    assert root.attributes["agenteval.run.status"] == "succeeded"
    assert root.attributes["agenteval.run.tool_calls"] == 1

    chats = [s for s in exporter.get_finished_spans() if s.name == "chat mock-model"]
    assert len(chats) == 2
    assert all(c.parent.span_id == root.context.span_id for c in chats)
    assert chats[0].attributes["gen_ai.system"] == "mock"
    assert chats[0].attributes["gen_ai.usage.input_tokens"] > 0

    tool = spans["execute_tool company_lookup"]
    assert tool.parent.span_id == root.context.span_id
    assert tool.attributes["gen_ai.tool.name"] == "company_lookup"

    # No prompt/response content on any span.
    for s in exporter.get_finished_spans():
        assert "Acme Corp" not in str(dict(s.attributes or {}))


def test_parallel_tool_spans_keep_their_parent(client):
    import tests.test_optimizations  # noqa: F401  (registers slow test tools)
    from app.providers import register_provider
    from app.providers.mock_provider import MockProvider

    exporter.clear()
    register_provider("mock", lambda: MockProvider(calls_per_turn=3))
    try:
        agent = client.post(
            "/agents",
            json={
                "name": "par",
                "provider": "mock",
                "model": "mock-model",
                "tools": ["test_slow_a", "test_slow_b", "test_slow_c"],
                "options": {"parallel_tool_calls": True},
            },
        ).json()
        client.post("/runs", json={"agent_id": agent["id"], "input": "x"})
    finally:
        register_provider("mock", MockProvider)
    spans = exporter.get_finished_spans()
    root = next(s for s in spans if s.name == "agent.run")
    tools = [s for s in spans if s.name.startswith("execute_tool")]
    assert len(tools) == 3
    assert {t.parent.span_id for t in tools} == {root.context.span_id}
    assert len({t.context.trace_id for t in tools + [root]}) == 1
