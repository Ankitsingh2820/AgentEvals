"""OpenAI-compatible adapter (OpenAI + Groq), tested with a fake client using the SDK's
real response and error types. No network, no spend."""

import json

import httpx2
import openai
import pytest
from openai.types.chat import ChatCompletion

from app.engine.executor import execute_run
from app.models import Agent, AgentVersion
from app.providers import get_provider, register_provider
from app.providers.base import (
    LLMRequest,
    Message,
    ProviderError,
    ToolCallRequest,
    ToolResult,
    ToolSpec,
)
from app.providers.mock_provider import MockProvider
from app.providers.openai_compatible import OpenAICompatibleProvider


def completion(content=None, tool_calls=None, finish="stop", prompt=100, completion_=20, cached=0):
    return ChatCompletion.model_validate(
        {
            "id": "c1",
            "object": "chat.completion",
            "created": 1,
            "model": "served-model",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": finish,
                    "message": {"role": "assistant", "content": content, "tool_calls": tool_calls},
                }
            ],
            "usage": {
                "prompt_tokens": prompt,
                "completion_tokens": completion_,
                "total_tokens": prompt + completion_,
                "prompt_tokens_details": {"cached_tokens": cached},
            },
        }
    )


def status_error(cls, code, message):
    req = httpx2.Request("POST", "https://api.example/v1/chat/completions")
    return cls(message, response=httpx2.Response(code, request=req), body=None)


class FakeCompletions:
    def __init__(self, *outcomes):
        self.outcomes, self.calls = list(outcomes), []

    def create(self, **params):
        self.calls.append(json.loads(json.dumps(params)))  # snapshot what was sent
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeClient:
    def __init__(self, *outcomes):
        self.chat = type("Chat", (), {})()
        self.chat.completions = FakeCompletions(*outcomes)
        self.models = type(
            "Models",
            (),
            {
                "list": lambda _self: [
                    type("M", (), {"id": "b-model"})(),
                    type("M", (), {"id": "a-model"})(),
                ]
            },
        )()


def provider(*outcomes, name="groq"):
    client = FakeClient(*outcomes)
    return OpenAICompatibleProvider(name, "key", "GROQ_API_KEY", client=client), client


REQ = dict(model="llama-x", system="You research companies.", max_tokens=500)
TOOL = ToolSpec(
    "company_lookup",
    "Look up",
    {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
)


def test_request_mapping_system_tools_and_tool_turns():
    p, client = provider(completion("done"))
    history = [
        Message("user", "Acme"),
        Message(
            "assistant",
            "",
            tool_calls=[ToolCallRequest("call_1", "company_lookup", {"name": "Acme"})],
        ),
        Message(
            "tool",
            tool_results=[
                ToolResult("call_1", '{"industry": "x"}'),
            ],
        ),
    ]
    p.complete(LLMRequest(**REQ, messages=history, tools=[TOOL], temperature=0.2))
    sent = client.chat.completions.calls[0]
    assert sent["messages"][0] == {"role": "system", "content": "You research companies."}
    assert sent["messages"][2]["tool_calls"][0]["function"] == {
        "name": "company_lookup",
        "arguments": '{"name": "Acme"}',
    }
    assert sent["messages"][3] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": '{"industry": "x"}',
    }
    assert sent["tools"][0]["function"]["parameters"]["required"] == ["name"]
    assert sent["max_completion_tokens"] == 500 and sent["temperature"] == 0.2


def test_response_parsing_tool_calls_usage_and_cache():
    p, _ = provider(
        completion(
            tool_calls=[
                {
                    "id": "call_9",
                    "type": "function",
                    "function": {"name": "company_lookup", "arguments": '{"name": "Globex"}'},
                }
            ],
            finish="tool_calls",
            prompt=1000,
            completion_=40,
            cached=768,
        )
    )
    r = p.complete(LLMRequest(**REQ, messages=[Message("user", "Globex")], tools=[TOOL]))
    assert r.tool_calls == [ToolCallRequest("call_9", "company_lookup", {"name": "Globex"})]
    assert r.stop_reason == "tool_use"
    assert (r.input_tokens, r.cache_read_input_tokens, r.output_tokens) == (232, 768, 40)
    assert r.response_model == "served-model"


@pytest.mark.parametrize(
    "finish,expected",
    [("stop", "end_turn"), ("length", "max_tokens"), ("content_filter", "refusal")],
)
def test_finish_reasons_map_to_engine_stop_reasons(finish, expected):
    p, _ = provider(completion("x", finish=finish))
    assert p.complete(LLMRequest(**REQ, messages=[Message("user", "hi")])).stop_reason == expected


def test_invalid_tool_arguments_are_kept_visible():
    p, _ = provider(
        completion(
            tool_calls=[
                {
                    "id": "c",
                    "type": "function",
                    "function": {"name": "company_lookup", "arguments": "{not json"},
                }
            ],
            finish="tool_calls",
        )
    )
    r = p.complete(LLMRequest(**REQ, messages=[Message("user", "hi")], tools=[TOOL]))
    assert r.tool_calls[0].arguments == {"__invalid_json__": "{not json"}


def test_rejected_temperature_is_dropped_and_recorded():
    p, client = provider(
        status_error(openai.BadRequestError, 400, "Unsupported value: 'temperature'"),
        completion("ok"),
    )
    r = p.complete(LLMRequest(**REQ, messages=[Message("user", "hi")], temperature=0.0))
    assert "temperature" in client.chat.completions.calls[0]
    assert "temperature" not in client.chat.completions.calls[1]
    assert "temperature" not in r.raw_request  # the trace shows what was really sent


def test_json_schema_falls_back_to_json_mode():
    p, client = provider(
        status_error(openai.BadRequestError, 400, "response_format json_schema not supported"),
        completion('{"reasoning": "r", "score": 4}'),
    )
    schema = {"type": "object", "properties": {"score": {"type": "integer"}}}
    r = p.complete(
        LLMRequest(**REQ, messages=[Message("user", "Reply with JSON")], json_schema=schema)
    )
    assert client.chat.completions.calls[0]["response_format"]["type"] == "json_schema"
    assert client.chat.completions.calls[1]["response_format"] == {"type": "json_object"}
    assert json.loads(r.text)["score"] == 4


@pytest.mark.parametrize(
    "error,retryable",
    [
        (status_error(openai.RateLimitError, 429, "slow down"), True),
        (status_error(openai.InternalServerError, 503, "overloaded"), True),
        (status_error(openai.AuthenticationError, 401, "bad key"), False),
        (status_error(openai.BadRequestError, 400, "invalid model"), False),
        (openai.APIConnectionError(request=httpx2.Request("POST", "https://x")), True),
    ],
)
def test_errors_become_provider_errors(error, retryable):
    p, _ = provider(error)
    with pytest.raises(ProviderError) as e:
        p.complete(LLMRequest(**REQ, messages=[Message("user", "hi")]))
    assert e.value.retryable is retryable
    assert e.value.args[0].startswith("groq")


def test_missing_key_fails_clearly_without_crashing():
    p = OpenAICompatibleProvider("openai", None, "OPENAI_API_KEY")
    with pytest.raises(ProviderError, match="OPENAI_API_KEY is not set") as e:
        p.complete(LLMRequest(**REQ, messages=[Message("user", "hi")]))
    assert e.value.retryable is False


def test_both_providers_registered_and_models_listed(client):
    assert {"openai", "groq"} <= set(client.get("/providers").json())
    register_provider("groq", lambda: provider()[0])
    try:
        assert client.get("/providers/groq/models").json() == {
            "provider": "groq",
            "models": ["a-model", "b-model"],
        }
    finally:
        register_provider("groq", lambda: OpenAICompatibleProvider("groq", None, "GROQ_API_KEY"))
    assert client.get("/providers/groq/models").status_code == 502  # no key configured
    assert client.get("/providers/mock/models").status_code == 400
    assert client.get("/providers/nope/models").status_code == 404


def test_agent_run_end_to_end_through_the_adapter(db):
    """The engine runs a tool-using agent on the adapter: tool call, tool result, answer."""
    p, client = provider(
        completion(
            tool_calls=[
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "company_lookup", "arguments": '{"name": "Acme Corp"}'},
                }
            ],
            finish="tool_calls",
        ),
        completion("Acme Corp is in Industrial Automation."),
    )
    register_provider("groq", lambda: p)
    try:
        agent = Agent(name="groq agent")
        agent.versions.append(
            AgentVersion(
                version=1,
                provider="groq",
                model="llama-x",
                system_prompt="s",
                max_tokens=100,
                tools=["company_lookup"],
            )
        )
        db.add(agent)
        db.commit()
        run = execute_run(db, agent.versions[0], "Acme Corp")
    finally:
        register_provider("groq", lambda: OpenAICompatibleProvider("groq", None, "GROQ_API_KEY"))
        register_provider("mock", MockProvider)
    assert run.status == "succeeded", run.error
    assert [s.type for s in run.steps] == ["llm_call", "tool_call", "llm_call"]
    assert "Industrial Automation" in run.steps[1].tool_call.result
    second = client.chat.completions.calls[1]["messages"]
    assert second[-1]["role"] == "tool" and second[-1]["tool_call_id"] == "call_1"


def test_registry_builds_from_settings_without_a_key():
    get_provider("openai")  # constructing never needs the key; the first call does


# --- prompt caching modes ------------------------------------------------------------------


def test_caching_modes_are_known_without_credentials():
    from app.providers import caching_mode

    assert caching_mode("anthropic") == "opt_in"
    assert caching_mode("mock") == "opt_in"
    assert caching_mode("openai") == caching_mode("groq") == "automatic"
    assert OpenAICompatibleProvider.prompt_caching == "automatic"


@pytest.mark.parametrize("provider_name", ["openai", "groq"])
def test_caching_switch_rejected_where_caching_is_automatic(client, provider_name):
    body = {
        "name": f"x-{provider_name}",
        "provider": provider_name,
        "model": "m",
        "options": {"prompt_caching": True},
    }
    r = client.post("/agents", json=body)
    assert r.status_code == 422
    assert "automatic" in r.json()["detail"]
    body["options"] = {"parallel_tool_calls": True}  # other options are fine
    assert client.post("/agents", json=body).status_code == 201


def test_caching_switch_allowed_where_it_is_opt_in(client):
    for p in ("anthropic", "mock"):
        r = client.post(
            "/agents",
            json={
                "name": f"y-{p}",
                "provider": p,
                "model": "m",
                "options": {"prompt_caching": True},
            },
        )
        assert r.status_code == 201, r.text


def test_analyzer_recommends_caching_only_for_opt_in_providers(db):
    import uuid

    from app.optimization.analyzer import analyze

    agent = Agent(name=f"long-{uuid.uuid4()}")
    mock_version = AgentVersion(
        version=1,
        provider="mock",
        model="mock-model",
        system_prompt="word " * 1500,
        max_tokens=100,
        tools=["company_lookup"],
        options={},
    )
    agent.versions.append(mock_version)
    db.add(agent)
    db.commit()
    runs = [execute_run(db, mock_version, "Acme Corp") for _ in range(2)]

    def types(provider_name):
        v = AgentVersion(provider=provider_name, model="m", options={}, tools=[])
        return {r["type"] for r in analyze(db, v, runs)}

    assert "prompt_caching" in types("mock")
    assert "prompt_caching" in types("anthropic")
    assert "prompt_caching" not in types("openai")  # already automatic: nothing to enable
    assert "prompt_caching" not in types("groq")


def test_provider_overrides_are_scoped_and_isolated_between_threads():
    import threading

    from app.providers import use_providers

    registry_groq = get_provider("groq")
    mine, _ = provider()
    seen = {}

    def other_user():  # another visitor's thread, running at the same time
        seen["other"] = get_provider("groq")

    with use_providers({"groq": mine}):
        assert get_provider("groq") is mine
        t = threading.Thread(target=other_user)
        t.start()
        t.join()
    assert seen["other"] is registry_groq  # never saw this user's provider (or key)
    assert get_provider("groq") is registry_groq  # restored after the block
