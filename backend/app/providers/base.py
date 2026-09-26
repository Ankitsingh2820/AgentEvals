"""Provider-neutral LLM interface.

The execution engine only speaks these types. Each provider adapter translates them to
and from its own SDK, so adding a provider never touches engine, tracing or storage code.
"""

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolCallRequest:
    """A tool invocation requested by the model."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    tool_call_id: str
    content: str
    is_error: bool = False


@dataclass
class Message:
    role: Literal["user", "assistant", "tool"]
    text: str = ""
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    # The provider's own serialization of an assistant turn. Replayed verbatim to the same
    # provider on the next request (some providers require e.g. reasoning blocks unchanged).
    provider_content: Any = None


@dataclass(frozen=True)
class LLMRequest:
    model: str
    system: str
    messages: list[Message]
    max_tokens: int
    temperature: float | None = None
    tools: list[ToolSpec] = field(default_factory=list)
    # When set, the provider must constrain its reply to JSON matching this schema
    # (used by LLM-as-a-judge). Providers without native support may ignore it.
    json_schema: dict[str, Any] | None = None
    # Ask the provider to cache the prompt prefix (system, tools, earlier turns) so later
    # calls in the same run re-read it at the cheaper cached-input rate.
    cache: bool = False


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCallRequest]
    stop_reason: str | None
    input_tokens: int
    output_tokens: int
    response_model: str | None
    # Exactly what was sent (after provider-specific adjustments) and received, for the trace.
    raw_request: dict[str, Any]
    raw_response: dict[str, Any]
    provider_request_id: str | None = None
    provider_content: Any = None
    # Prompt-cache usage. input_tokens excludes these (they are billed at different rates).
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


class ProviderError(Exception):
    """A provider call failed. `retryable` distinguishes transient from permanent failures."""

    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


CachingMode = Literal["opt_in", "automatic"]


class LLMProvider(Protocol):
    name: str
    # How the provider caches prompt prefixes:
    #   opt_in    - only when the request asks (LLMRequest.cache); the agent option matters
    #   automatic - the provider caches long prefixes by itself; the option has no effect
    prompt_caching: CachingMode

    def complete(self, request: LLMRequest) -> LLMResponse: ...
