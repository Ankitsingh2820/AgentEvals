import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import (
    agents,
    datasets,
    evaluations,
    evaluators,
    experiments,
    metrics,
    optimizations,
    pricing,
    runs,
)
from app.auth import ensure_bootstrap_key, require_api_key
from app.config import get_settings
from app.logging_config import configure_logging, request_id_var
from app.providers import available_providers
from app.ratelimit import rate_limit
from app.telemetry import setup_telemetry
from app.tools import available_tools

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger("app.main")


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.bootstrap_api_key:
        from app.db import SessionLocal

        with SessionLocal() as db:
            if ensure_bootstrap_key(db, settings.bootstrap_api_key):
                logger.info("bootstrap API key registered")
    if not settings.auth_enabled:
        logger.warning("API authentication is DISABLED (AGENTEVAL_AUTH_ENABLED=false)")
    yield


app = FastAPI(
    title="AgentEval",
    version="1.0.0",
    description="Evaluate and optimize LLM agents on measured quality, cost and latency. "
    "Authenticate with `Authorization: Bearer <api key>`.",
    lifespan=lifespan,
)
setup_telemetry(app)


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Tag every request (and its log lines) with an id, echoed as X-Request-ID.

    Unhandled exceptions are turned into the error envelope here, while the request id is
    still in scope: the log line and the response carry the same id, and clients never
    see internals (SQL, stack traces, connection strings)."""
    rid = (request.headers.get("x-request-id") or uuid.uuid4().hex)[:64]
    token = request_id_var.set(rid)
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("unhandled error", extra={"path": request.url.path})
        response = JSONResponse(
            {"detail": "internal server error", "request_id": rid}, status_code=500
        )
    finally:
        request_id_var.reset(token)
    response.headers["X-Request-ID"] = rid
    return response


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        {"detail": exc.detail, "request_id": request_id_var.get()},
        status_code=exc.status_code,
        headers=getattr(exc, "headers", None),
    )


protected = [Depends(require_api_key), Depends(rate_limit("default"))]
for module in (
    agents,
    runs,
    pricing,
    metrics,
    datasets,
    evaluators,
    evaluations,
    experiments,
    optimizations,
):
    app.include_router(module.router, dependencies=protected)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    """Liveness probe. The only unauthenticated endpoint."""
    return {"status": "ok"}


@app.get("/providers", tags=["meta"], dependencies=protected)
def providers() -> list[str]:
    return available_providers()


@app.get("/providers/{name}/models", tags=["meta"], dependencies=protected)
def provider_models(name: str) -> dict:
    """Models available to a provider's configured key. Free to call, so it doubles as a
    check that the key in .env works before spending anything on runs."""
    from fastapi import HTTPException

    from app.providers import get_provider
    from app.providers.base import ProviderError

    try:
        provider = get_provider(name)
    except KeyError as e:
        raise HTTPException(404, f"unknown provider '{name}'") from e
    if not hasattr(provider, "list_models"):
        raise HTTPException(400, f"provider '{name}' cannot list models")
    try:
        return {"provider": name, "models": provider.list_models()}
    except ProviderError as e:
        raise HTTPException(502, str(e)) from e


@app.get("/tools", tags=["meta"], dependencies=protected)
def tools() -> list[dict]:
    return [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema}
        for t in available_tools()
    ]


@app.get("/auth/whoami", tags=["meta"])
def whoami(principal=Depends(require_api_key)) -> dict[str, str]:
    """Check a key: returns the key's name (used by the dashboard's sign-in)."""
    return {"id": principal.id, "name": principal.name}
