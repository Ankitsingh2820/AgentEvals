import uuid

import pytest

from app.engine.executor import execute_run
from app.models import Agent, AgentVersion
from app.providers import register_provider
from app.providers.base import LLMResponse, ProviderError, ToolCallRequest
from app.providers.mock_provider import MockProvider


def _version(db, tools=("company_lookup",), provider="mock") -> AgentVersion:
    agent = Agent(name=f"agent-{uuid.uuid4()}")
    version = AgentVersion(
        version=1,
        provider=provider,
        model="mock-model",
        system_prompt="You research companies.",
        max_tokens=1000,
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


def test_run_with_tool_call_records_full_trace(db):
    register_provider(
        "mock", lambda: MockProvider(tool_arguments={"company_lookup": {"name": "Acme Corp"}})
    )
    run = execute_run(db, _version(db), "Research Acme Corp")

    assert run.status == "succeeded", run.error
    assert [s.type for s in run.steps] == ["llm_call", "tool_call", "llm_call"]
    assert [s.sequence for s in run.steps] == [1, 2, 3]
    assert run.llm_call_count == 2 and run.tool_call_count == 1
    assert run.tool_failure_count == 0
    assert "Industrial Automation" in run.final_output

    llm_steps = [s for s in run.steps if s.type == "llm_call"]
    assert run.input_tokens == sum(s.llm_call.input_tokens for s in llm_steps)
    assert run.output_tokens == sum(s.llm_call.output_tokens for s in llm_steps)
    for s in llm_steps:
        assert s.llm_call.total_tokens == s.llm_call.input_tokens + s.llm_call.output_tokens

    # Timeline is monotonic and contained within the run's total latency.
    offsets = [s.start_offset_ms for s in run.steps]
    assert offsets == sorted(offsets)
    last = run.steps[-1]
    assert last.start_offset_ms + last.latency_ms <= run.total_latency_ms
    assert run.ended_at is not None


def test_failed_tool_is_reported_to_model_and_run_continues(db):
    register_provider(
        "mock", lambda: MockProvider(tool_arguments={"company_lookup": {"name": "Nonexistent Inc"}})
    )
    run = execute_run(db, _version(db), "Research Nonexistent Inc")

    assert run.status == "succeeded"
    tool_step = run.steps[1]
    assert tool_step.status == "failed"
    assert "LookupError" in tool_step.error
    assert tool_step.tool_call.success is False
    assert run.tool_failure_count == 1
    assert "Error: LookupError" in run.final_output  # the model saw the error result


def test_run_without_tools_is_single_llm_call(db):
    run = execute_run(db, _version(db, tools=()), "Hello")
    assert run.status == "succeeded"
    assert [s.type for s in run.steps] == ["llm_call"]


class _ScriptedProvider:
    name = "mock"

    def __init__(self, behaviour):
        self.behaviour = behaviour

    def complete(self, request):
        return self.behaviour(request)


def _response(**overrides) -> LLMResponse:
    base = dict(
        text="",
        tool_calls=[],
        stop_reason="end_turn",
        input_tokens=1,
        output_tokens=1,
        response_model="m",
        raw_request={},
        raw_response={},
    )
    return LLMResponse(**{**base, **overrides})


def test_provider_error_fails_run_and_is_traced(db):
    def boom(_):
        raise ProviderError("anthropic 529: overloaded", retryable=True)

    register_provider("mock", lambda: _ScriptedProvider(boom))
    run = execute_run(db, _version(db), "hi")

    # Transient (retryable) failure: retried twice, every attempt visible in the trace.
    assert run.status == "failed"
    assert "overloaded" in run.error
    assert [st.status for st in run.steps] == ["failed", "failed", "failed"]
    assert run.llm_call_count == 3 and run.retry_count == 2


def test_max_iterations_stops_looping_agent(db):
    loop = _response(
        stop_reason="tool_use",
        tool_calls=[ToolCallRequest(id="t", name="calculator", arguments={"expression": "1+1"})],
    )
    register_provider("mock", lambda: _ScriptedProvider(lambda _: loop))
    run = execute_run(db, _version(db, tools=("calculator",)), "loop forever")

    assert run.status == "failed"
    assert "max iterations" in run.error
    assert run.llm_call_count == 10 and run.tool_call_count == 10


def test_refusal_and_truncation_are_failures(db):
    register_provider("mock", lambda: _ScriptedProvider(lambda _: _response(stop_reason="refusal")))
    assert execute_run(db, _version(db, tools=()), "x").error == "model refused the request"

    register_provider(
        "mock",
        lambda: _ScriptedProvider(lambda _: _response(text="partial", stop_reason="max_tokens")),
    )
    run = execute_run(db, _version(db, tools=()), "x")
    assert run.status == "failed" and run.final_output == "partial"


def test_unknown_tool_requested_by_model(db):
    calls = iter(
        [
            _response(
                stop_reason="tool_use",
                tool_calls=[ToolCallRequest(id="t", name="made_up", arguments={})],
            ),
            _response(text="done"),
        ]
    )
    register_provider("mock", lambda: _ScriptedProvider(lambda _: next(calls)))
    run = execute_run(db, _version(db, tools=("calculator",)), "x")

    assert run.status == "succeeded"
    assert run.steps[1].error == "unknown tool 'made_up'"


def test_mock_provider_fills_tool_arguments_from_schema(db):
    run = execute_run(db, _version(db), "Acme Corp")
    assert run.steps[1].tool_call.arguments == {"name": "Acme Corp"}
    assert run.tool_failure_count == 0
