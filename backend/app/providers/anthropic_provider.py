"""Anthropic (Claude) adapter for the provider-neutral LLM interface."""

import logging
from typing import Any

import anthropic

from app.providers.base import LLMRequest, LLMResponse, Message, ProviderError, ToolCallRequest

logger = logging.getLogger(__name__)

# Models that reject sampling parameters (temperature/top_p/top_k) with a 400.
_NO_SAMPLING_PREFIXES = (
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-fable-5",
    "claude-mythos-5",
    "claude-opus-4-7",
    "claude-opus-4-8",
)


def supports_temperature(model: str) -> bool:
    return not model.startswith(_NO_SAMPLING_PREFIXES)


class AnthropicProvider:
    name = "anthropic"
    prompt_caching = "opt_in"  # cache_control is sent only when the request asks

    def __init__(self, client: Any | None = None, timeout: float = 120.0):
        # Credentials are resolved by the SDK from the environment (ANTHROPIC_API_KEY etc.).
        # SDK retries are off: the executor retries transient failures itself so that
        # every attempt is visible in the trace and counted in the retry metrics.
        self._client = client or anthropic.Anthropic(timeout=timeout, max_retries=0)

    def complete(self, request: LLMRequest) -> LLMResponse:
        params: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "messages": [_to_anthropic_message(m) for m in request.messages],
        }
        if request.system:
            params["system"] = request.system
        if request.tools:
            params["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.input_schema}
                for t in request.tools
            ]
        if request.cache:
            # Automatic caching: the API caches up to the last cacheable block, so each
            # turn of the agent loop re-reads the previous turns from cache. Prefixes below
            # the model's minimum (512-4096 tokens) silently aren't cached; the usage
            # fields record what actually happened.
            params["cache_control"] = {"type": "ephemeral"}
        if request.json_schema is not None:
            params["output_config"] = {
                "format": {"type": "json_schema", "schema": request.json_schema}
            }
        if request.temperature is not None:
            if supports_temperature(request.model):
                params["temperature"] = request.temperature
            else:
                # Recorded in the trace: the stored request shows temperature was not sent.
                logger.warning(
                    "temperature not supported by model; omitted",
                    extra={"model": request.model},
                )

        try:
            response = self._client.messages.create(**params)
        except anthropic.APIStatusError as e:
            retryable = e.status_code == 429 or e.status_code >= 500
            message = f"anthropic {e.status_code}: {e.message}"
            raise ProviderError(message, retryable=retryable) from e
        except anthropic.APIConnectionError as e:
            raise ProviderError(f"anthropic connection error: {e}", retryable=True) from e

        text = "".join(b.text for b in response.content if b.type == "text")
        tool_calls = [
            ToolCallRequest(id=b.id, name=b.name, arguments=dict(b.input))
            for b in response.content
            if b.type == "tool_use"
        ]
        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            stop_reason=response.stop_reason,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            response_model=response.model,
            raw_request=_jsonable(params),
            raw_response=response.to_dict(),
            provider_request_id=getattr(response, "_request_id", None),
            provider_content=response.content,
            cache_creation_input_tokens=response.usage.cache_creation_input_tokens or 0,
            cache_read_input_tokens=response.usage.cache_read_input_tokens or 0,
        )


def _to_anthropic_message(m: Message) -> dict[str, Any]:
    if m.role == "user":
        return {"role": "user", "content": m.text}
    if m.role == "tool":
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": r.tool_call_id,
                    "content": r.content,
                    "is_error": r.is_error,
                }
                for r in m.tool_results
            ],
        }
    # Assistant: replay the exact blocks Claude produced (thinking blocks must round-trip
    # unchanged during tool use); fall back to rebuilding from neutral fields.
    if m.provider_content is not None:
        return {"role": "assistant", "content": m.provider_content}
    content: list[dict[str, Any]] = []
    if m.text:
        content.append({"type": "text", "text": m.text})
    content += [
        {"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments} for c in m.tool_calls
    ]
    return {"role": "assistant", "content": content}


def _jsonable(value: Any) -> Any:
    """Convert SDK content-block objects inside a request into plain JSON for storage."""
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if hasattr(value, "to_dict"):
        return value.to_dict()
    return value
