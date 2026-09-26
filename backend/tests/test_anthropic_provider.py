"""Tests for the Anthropic adapter's request/response translation, using a fake client."""

from anthropic.types import Message as AnthropicMessage

from app.providers.anthropic_provider import AnthropicProvider
from app.providers.base import (
    LLMRequest,
    Message,
    ToolCallRequest,
    ToolResult,
    ToolSpec,
)


class _FakeMessages:
    def __init__(self, response):
        self.response = response
        self.params = None

    def create(self, **params):
        self.params = params
        return self.response


class _FakeClient:
    def __init__(self, response):
        self.messages = _FakeMessages(response)


def _sdk_message(content, stop_reason="end_turn", usage=None):
    return AnthropicMessage.model_validate(
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5",
            "content": content,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": usage or {"input_tokens": 120, "output_tokens": 30},
        }
    )


def _request(model="claude-opus-5", temperature=0.0, messages=None):
    return LLMRequest(
        model=model,
        system="sys",
        max_tokens=500,
        temperature=temperature,
        messages=messages or [Message(role="user", text="hi")],
        tools=[ToolSpec("calculator", "calc", {"type": "object", "properties": {}})],
    )


def test_parses_tool_use_and_usage():
    client = _FakeClient(
        _sdk_message(
            [
                {"type": "text", "text": "Let me compute."},
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "calculator",
                    "input": {"expression": "2+2"},
                },
            ],
            stop_reason="tool_use",
        )
    )
    resp = AnthropicProvider(client=client).complete(_request())

    assert resp.text == "Let me compute."
    assert resp.tool_calls == [ToolCallRequest("toolu_1", "calculator", {"expression": "2+2"})]
    assert (resp.input_tokens, resp.output_tokens) == (120, 30)
    assert resp.stop_reason == "tool_use"
    assert resp.response_model == "claude-opus-5"
    assert client.messages.params["tools"][0]["name"] == "calculator"
    assert client.messages.params["system"] == "sys"


def test_temperature_omitted_for_models_that_reject_it():
    client = _FakeClient(_sdk_message([{"type": "text", "text": "ok"}]))
    resp = AnthropicProvider(client=client).complete(_request(model="claude-opus-5"))
    assert "temperature" not in client.messages.params
    assert "temperature" not in resp.raw_request  # the trace shows what was really sent

    AnthropicProvider(client=client).complete(_request(model="claude-haiku-4-5"))
    assert client.messages.params["temperature"] == 0.0


def test_translates_tool_turns_and_replays_provider_content():
    first = _sdk_message(
        [{"type": "tool_use", "id": "toolu_1", "name": "calculator", "input": {"expression": "1"}}],
        stop_reason="tool_use",
    )
    history = [
        Message(role="user", text="hi"),
        Message(
            role="assistant",
            tool_calls=[ToolCallRequest("toolu_1", "calculator", {})],
            provider_content=first.content,
        ),
        Message(role="tool", tool_results=[ToolResult("toolu_1", "boom", is_error=True)]),
    ]
    client = _FakeClient(_sdk_message([{"type": "text", "text": "done"}]))
    resp = AnthropicProvider(client=client).complete(_request(messages=history))

    sent = client.messages.params["messages"]
    assert sent[1]["content"] is first.content  # exact blocks replayed
    assert sent[2] == {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": "boom", "is_error": True}
        ],
    }
    # Stored request is plain JSON (SDK blocks converted).
    assert resp.raw_request["messages"][1]["content"][0]["type"] == "tool_use"


def test_parses_prompt_cache_usage():
    usage = {
        "input_tokens": 50,
        "output_tokens": 5,
        "cache_creation_input_tokens": 2048,
        "cache_read_input_tokens": 4096,
    }
    client = _FakeClient(_sdk_message([{"type": "text", "text": "ok"}], usage=usage))
    resp = AnthropicProvider(client=client).complete(_request())
    assert resp.input_tokens == 50
    assert (resp.cache_creation_input_tokens, resp.cache_read_input_tokens) == (2048, 4096)
