"""Provider registry. Providers are constructed lazily so a missing SDK credential only
fails runs that actually use that provider."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from app.config import get_settings
from app.providers.base import LLMProvider

_factories: dict[str, Callable[[], LLMProvider]] = {}
_instances: dict[str, LLMProvider] = {}
_caching: dict[str, str] = {}
# Per-context provider instances (e.g. one per Streamlit visitor, with that visitor's own
# API key). A ContextVar is isolated per thread/async task, so one user's override can
# never be seen by another user's run.
_overrides: ContextVar[dict[str, LLMProvider] | None] = ContextVar(
    "provider_overrides", default=None
)


@contextmanager
def use_providers(providers: dict[str, LLMProvider]) -> Iterator[None]:
    """Within this block, get_provider(name) returns these instances instead of the
    registry's. Nothing global is changed."""
    token = _overrides.set({**(_overrides.get() or {}), **providers})
    try:
        yield
    finally:
        _overrides.reset(token)


def register_provider(
    name: str, factory: Callable[[], LLMProvider], prompt_caching: str | None = None
) -> None:
    """`prompt_caching` ('opt_in' | 'automatic') is recorded at registration so it can be
    read without constructing the provider (which may need credentials). Re-registering
    without it keeps the previous mode."""
    _factories[name] = factory
    _instances.pop(name, None)
    if prompt_caching is not None:
        _caching[name] = prompt_caching


def caching_mode(name: str) -> str:
    """'opt_in' or 'automatic' (see LLMProvider.prompt_caching); no credentials needed."""
    return _caching.get(name, "opt_in")


def available_providers() -> list[str]:
    return sorted(_factories)


def get_provider(name: str) -> LLMProvider:
    overrides = _overrides.get()
    if overrides and name in overrides:
        return overrides[name]
    if name not in _factories:
        raise KeyError(f"unknown provider '{name}'")
    if name not in _instances:
        _instances[name] = _factories[name]()
    return _instances[name]


def _anthropic() -> LLMProvider:
    from app.providers.anthropic_provider import AnthropicProvider

    return AnthropicProvider(timeout=get_settings().llm_timeout_seconds)


def _mock() -> LLMProvider:
    from app.providers.mock_provider import MockProvider

    return MockProvider()


def _openai_compatible(name: str) -> Callable[[], LLMProvider]:
    def factory() -> LLMProvider:
        from app.providers.openai_compatible import OpenAICompatibleProvider

        settings = get_settings()
        if name == "openai":
            key, env, base = settings.openai_api_key, "OPENAI_API_KEY", settings.openai_base_url
        else:
            key, env, base = settings.groq_api_key, "GROQ_API_KEY", settings.groq_base_url
        return OpenAICompatibleProvider(
            name=name,
            api_key=key.get_secret_value() if key else None,
            key_env_var=env,
            base_url=base,
            timeout=settings.llm_timeout_seconds,
        )

    return factory


register_provider("anthropic", _anthropic, prompt_caching="opt_in")
register_provider("openai", _openai_compatible("openai"), prompt_caching="automatic")
register_provider("groq", _openai_compatible("groq"), prompt_caching="automatic")
register_provider("mock", _mock, prompt_caching="opt_in")  # simulated opt-in cache
