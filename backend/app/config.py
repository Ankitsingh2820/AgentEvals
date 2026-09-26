"""Application settings, loaded from environment variables (and an optional .env file).

Secrets such as ANTHROPIC_API_KEY are never stored here or in the database; the
provider SDKs read them from the environment directly. AgentEval's own API keys are
stored only as SHA-256 hashes (see app/auth.py).
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="AGENTEVAL_", extra="ignore")

    database_url: str = "postgresql+psycopg://agenteval:agenteval@localhost:5432/agenteval"
    log_level: str = "INFO"
    # Hard ceiling on LLM<->tool round trips per run, so a looping agent cannot run forever.
    max_agent_iterations: int = 10
    # Timeout (seconds) for a single provider request.
    llm_timeout_seconds: float = 120.0
    # Retries of transient provider failures (429/5xx/connection), each recorded as a
    # failed step in the trace. Backoff doubles from llm_retry_base_seconds.
    llm_max_retries: int = 2
    llm_retry_base_seconds: float = 1.0

    # --- LLM providers ----------------------------------------------------------------------
    # Standard (unprefixed) variable names, so the same .env works for the SDKs and here.
    # SecretStr keeps the values out of reprs, logs and error messages.
    openai_api_key: SecretStr | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    groq_api_key: SecretStr | None = Field(default=None, validation_alias="GROQ_API_KEY")
    openai_base_url: str | None = None  # None = api.openai.com; set for Azure/proxies
    groq_base_url: str = "https://api.groq.com/openai/v1"

    # --- Security -----------------------------------------------------------------------
    # Require an API key on every endpoint except /health. Disable only for local tests.
    auth_enabled: bool = True
    # If set and no key exists yet, this key is registered at startup (hashed), so a fresh
    # deployment is usable without a manual step. Treat it like any other secret.
    bootstrap_api_key: str | None = None
    # Requests per minute, per API key (or per client IP when auth is disabled).
    rate_limit_per_minute: int = 120
    # Stricter limit for endpoints that execute agents and spend provider money.
    execute_rate_limit_per_minute: int = 20

    # --- Background work ------------------------------------------------------------------
    # "inline": FastAPI background task in the API process (dev/tests).
    # "celery": durable queue on Redis, run by `celery -A app.worker worker`.
    task_backend: Literal["inline", "celery"] = "inline"
    redis_url: str | None = None  # also backs the rate limiter when set
    # A running evaluation/experiment is owned by one task execution through a lease that
    # is renewed after every case. It must exceed the longest single case (agent run +
    # evaluators). A duplicate delivery waits until the lease lapses.
    task_lease_seconds: int = 600
    # Redis redelivers a task whose worker died without acknowledging it after this long.
    # Leases make early redelivery harmless, so it can be much shorter than a long task.
    task_visibility_timeout_seconds: int = 900
    # Each worker re-queues work abandoned by a dead worker (running, lease lapsed) this
    # often, so crash recovery takes about lease + interval.
    task_recovery_interval_seconds: int = 30

    # --- Observability --------------------------------------------------------------------
    # OpenTelemetry spans are exported via OTLP/HTTP when this (or the standard
    # OTEL_EXPORTER_OTLP_ENDPOINT) is set; otherwise tracing is a no-op.
    otel_exporter_endpoint: str | None = None
    service_name: str = "agenteval-api"


@lru_cache
def get_settings() -> Settings:
    return Settings()
