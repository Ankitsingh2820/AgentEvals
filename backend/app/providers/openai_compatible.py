"""OpenAI-compatible adapter: OpenAI itself, and Groq (whose API is OpenAI-compatible).

Uses the official `openai` SDK with a per-provider base URL and key. Model capabilities
differ (some reject `temperature`, some don't support strict JSON-schema output), so
instead of hard-coding model lists the adapter adapts at request time: if the API rejects
one of those parameters it retries once without it, and the stored request records
exactly what was finally sent.
"""

import json
import logging
from typing import Any

import openai

from app.providers.base import LLMRequest, LLMResponse, Message, ProviderError, ToolCallRequest

logger = logging.getLogger(__name__)

# Chat Completions finish reasons -> the neutral stop reasons the engine understands.
_STOP = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "length": "max_tokens",
    "content_filter": "refusal",
}


class OpenAICompatibleProvider:
    # OpenAI and Groq cache long prompt prefixes automatically; there is nothing to switch on.
    prompt_caching = "automatic"

    def __init__(
        self,
        name: str,
        api_key: str | None,
        key_env_var: str,
        base_url: str | None = None,
        timeout: float = 120.0,
        client: Any | None = None,
    ):
        self.name = name
        self._key_env_var = key_env_var
        self._api_key = api_key
        self._base_url = base_url
        self._timeout = timeout
        self._client = client  # injected in tests; otherwise built on first use

    def _get_client(self):
        if self._client is None:
            if not self._api_key:
                raise ProviderError(
                    f"{self._key_env_var} is not set: add it to .env and restart", retryable=False
                )
            # SDK retries off: the executor retries transient failures itself, visibly.
            self._client = openai.OpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
                timeout=self._timeout,
                max_retries=0,
            )
        return self._client

    def list_models(self) -> list[str]:
        try:
            return sorted(m.id for m in self._get_client().models.list())
        except openai.APIStatusError as e:
            raise ProviderError(f"{self.name} {e.status_code}: {e.message}") from e
        except openai.APIConnectionError as e:
            raise ProviderError(f"{self.name} connection error: {e}", retryable=True) from e

    def complete(self, request: LLMRequest) -> LLMResponse:
        params: dict[str, Any] = {
            "model": request.model,
            "messages": _to_openai_messages(request.system, request.messages),
            "max_completion_tokens": request.max_tokens,
        }
        if request.tools:
            params["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.input_schema,
                    },
                }
                for t in request.tools
            ]
        if request.temperature is not None:
            params["temperature"] = request.temperature
        if request.json_schema is not None:
            params["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "response", "schema": request.json_schema, "strict": True},
            }
        # `request.cache` needs no parameter: both providers cache long prompt prefixes
        # automatically, and the usage figures report what was actually read from cache.

        response = self._create(params)
        return _parse(response, params)

    def _create(self, params: dict[str, Any]):
        client = self._get_client()
        for _ in range(3):  # at most: drop temperature, downgrade response_format, send
            try:
                return client.chat.completions.create(**params)
            except openai.BadRequestError as e:
                message = str(e.message).lower()
                if "temperature" in params and "temperature" in message:
                    logger.warning(
                        "model rejected temperature; retrying without it",
                        extra={"provider": self.name, "model": params["model"]},
                    )
                    params.pop("temperature")
                    continue
                fmt = params.get("response_format", {})
                if fmt.get("type") == "json_schema" and (
                    "response_format" in message or "json_schema" in message
                ):
                    logger.warning(
                        "model lacks JSON-schema output; using JSON mode",
                        extra={"provider": self.name, "model": params["model"]},
                    )
                    params["response_format"] = {"type": "json_object"}
                    continue
                raise ProviderError(f"{self.name} 400: {e.message}") from e
            except openai.APIStatusError as e:
                retryable = e.status_code == 429 or e.status_code >= 500
                raise ProviderError(
                    f"{self.name} {e.status_code}: {e.message}", retryable=retryable
                ) from e
            except openai.APIConnectionError as e:  # includes timeouts
                raise ProviderError(f"{self.name} connection error: {e}", retryable=True) from e
        raise ProviderError(f"{self.name}: request rejected after parameter fallbacks")


def _to_openai_messages(system: str, messages: list[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if system:
        out.append({"role": "system", "content": system})
    for m in messages:
        if m.role == "user":
            out.append({"role": "user", "content": m.text})
        elif m.role == "assistant":
            msg: dict[str, Any] = {"role": "assistant", "content": m.text or None}
            if m.tool_calls:
                msg["tool_calls"] = [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                    }
                    for c in m.tool_calls
                ]
            out.append(msg)
        else:  # tool results: one message per call, matched by id
            out += [
                {"role": "tool", "tool_call_id": r.tool_call_id, "content": r.content}
                for r in m.tool_results
            ]
    return out


def _parse_arguments(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        # Keep the malformed text visible: the tool call fails and the model sees the error.
        return {"__invalid_json__": raw}
    return value if isinstance(value, dict) else {"__invalid_json__": raw}


def _parse(response, params: dict[str, Any]) -> LLMResponse:
    choice = response.choices[0]
    message = choice.message
    tool_calls = [
        ToolCallRequest(
            id=c.id, name=c.function.name, arguments=_parse_arguments(c.function.arguments)
        )
        for c in (message.tool_calls or [])
        if getattr(c, "function", None) is not None
    ]
    usage = response.usage
    prompt = usage.prompt_tokens if usage else 0
    details = getattr(usage, "prompt_tokens_details", None) if usage else None
    cached = (getattr(details, "cached_tokens", None) or 0) if details else 0
    return LLMResponse(
        text=message.content or "",
        tool_calls=tool_calls,
        stop_reason=_STOP.get(choice.finish_reason, choice.finish_reason),
        # Neutral convention: input_tokens excludes cache reads (priced separately).
        input_tokens=max(prompt - cached, 0),
        output_tokens=usage.completion_tokens if usage else 0,
        cache_read_input_tokens=cached,
        response_model=response.model,
        raw_request=params,
        raw_response=response.to_dict(),
        provider_request_id=getattr(response, "_request_id", None),
    )
