"""Authentication, rate limiting, error envelope, request ids, log redaction, retries."""

import json
import logging
import uuid

import pytest

from app.auth import create_api_key, ensure_bootstrap_key, hash_key
from app.config import get_settings
from app.db import get_db
from app.engine.executor import execute_run
from app.logging_config import JsonFormatter, redact
from app.main import app
from app.models import Agent, AgentVersion, ApiKey
from app.providers import register_provider
from app.providers.base import ProviderError
from app.providers.mock_provider import MockProvider


@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setattr(get_settings(), "auth_enabled", True)


def test_requests_without_a_valid_key_are_rejected(client, auth_on):
    r = client.get("/agents")
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"
    assert r.json()["detail"] == "missing or invalid API key"
    assert r.json()["request_id"]
    assert client.get("/agents", headers={"Authorization": "Bearer ae_wrong"}).status_code == 401
    assert client.get("/health").status_code == 200  # the only public endpoint


def test_valid_key_via_bearer_or_header(client, db, auth_on):
    record, key = create_api_key(db, "ci")
    assert client.get("/agents", headers={"Authorization": f"Bearer {key}"}).status_code == 200
    assert client.get("/tools", headers={"X-API-Key": key}).status_code == 200
    who = client.get("/auth/whoami", headers={"X-API-Key": key}).json()
    assert who == {"id": record.id, "name": "ci"}
    db.refresh(record)
    assert record.last_used_at is not None


def test_keys_are_stored_hashed_and_can_be_revoked(client, db, auth_on):
    record, key = create_api_key(db, "temp")
    assert key.startswith("ae_") and len(key) > 40
    stored = db.get(ApiKey, record.id)
    assert stored.key_hash == hash_key(key) and key not in (stored.key_hash, stored.key_prefix)

    from datetime import UTC, datetime

    stored.revoked_at = datetime.now(UTC)
    db.commit()
    assert client.get("/agents", headers={"X-API-Key": key}).status_code == 401


def test_bootstrap_key_only_when_no_active_key_exists(db):
    from datetime import UTC, datetime

    first, second, third = ("ae_" + c * 40 for c in "bcd")
    assert ensure_bootstrap_key(db, first) is True
    assert ensure_bootstrap_key(db, second) is False  # an active key exists
    assert db.query(ApiKey).count() == 1

    for k in db.query(ApiKey):
        k.revoked_at = datetime.now(UTC)
    db.commit()
    assert ensure_bootstrap_key(db, first) is False  # a revoked key is never re-activated
    assert ensure_bootstrap_key(db, third) is True  # a new key recovers access
    assert db.query(ApiKey).filter(ApiKey.revoked_at.is_(None)).count() == 1


def test_default_rate_limit_returns_429_with_retry_after(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_limit_per_minute", 3)
    codes = [client.get("/agents").status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    r = client.get("/agents")
    assert int(r.headers["retry-after"]) >= 1
    assert "rate limit exceeded" in r.json()["detail"]


def test_execute_endpoints_have_a_stricter_limit(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "execute_rate_limit_per_minute", 1)
    agent = client.post("/agents", json={"name": "x", "provider": "mock", "model": "m"}).json()
    body = {"agent_id": agent["id"], "input": "hi"}
    assert client.post("/runs", json=body).status_code == 201
    assert client.post("/runs", json=body).status_code == 429
    assert client.get("/runs").status_code == 200  # reads are unaffected


def test_rate_limits_are_per_key(client, db, auth_on, monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_limit_per_minute", 1)
    _, a = create_api_key(db, "a")
    _, b = create_api_key(db, "b")
    assert client.get("/agents", headers={"X-API-Key": a}).status_code == 200
    assert client.get("/agents", headers={"X-API-Key": a}).status_code == 429
    assert client.get("/agents", headers={"X-API-Key": b}).status_code == 200


def test_request_id_is_generated_or_propagated(client):
    assert len(client.get("/health").headers["x-request-id"]) == 32
    assert (
        client.get("/health", headers={"X-Request-ID": "abc-123"}).headers["x-request-id"]
        == "abc-123"
    )


def test_unhandled_errors_do_not_leak_internals(client):
    def broken_db():
        raise RuntimeError("password=hunter2 in connection string")

    app.dependency_overrides[get_db] = broken_db
    from fastapi.testclient import TestClient

    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/agents")
    assert r.status_code == 500
    assert r.json()["detail"] == "internal server error"
    assert "hunter2" not in r.text
    assert r.json()["request_id"] == r.headers["x-request-id"]


def test_secrets_are_redacted_from_logs():
    text = (
        "key sk-ant-api03-AbCdEf123456789 and ae_"
        + "x" * 43
        + " header Bearer abcdefghijklmnopqrstu"
    )
    out = redact(text)
    assert "sk-ant-api03" not in out and "ae_xxx" not in out and "abcdefghijklmnop" not in out
    assert out.count("[REDACTED]") == 3

    record = logging.makeLogRecord(
        {
            "msg": "calling with sk-ant-api03-SECRETSECRET",
            "levelname": "INFO",
            "name": "t",
            "detail": {"auth": "Bearer " + "z" * 30},
        }
    )
    line = json.loads(JsonFormatter().format(record))
    assert "SECRETSECRET" not in line["msg"] and "zzzz" not in line["detail"]["auth"]


class _Failing:
    name = "mock"

    def __init__(self, retryable):
        self.retryable, self.calls = retryable, 0

    def complete(self, request):
        self.calls += 1
        raise ProviderError("anthropic 400: bad request", retryable=self.retryable)


def test_permanent_provider_errors_are_not_retried(db):
    provider = _Failing(retryable=False)
    register_provider("mock", lambda: provider)
    try:
        agent = Agent(name=f"a-{uuid.uuid4()}")
        agent.versions.append(
            AgentVersion(
                version=1, provider="mock", model="m", system_prompt="", max_tokens=10, tools=[]
            )
        )
        db.add(agent)
        db.commit()
        run = execute_run(db, agent.versions[0], "hi")
    finally:
        register_provider("mock", MockProvider)
    assert provider.calls == 1
    assert run.retry_count == 0 and run.status == "failed"


def test_openapi_declares_the_api_key_so_docs_show_authorize(client):
    spec = client.get("/openapi.json").json()
    schemes = spec["components"]["securitySchemes"]
    assert {s["type"] for s in schemes.values()} == {"http", "apiKey"}
    assert any(s.get("scheme") == "bearer" for s in schemes.values())
    # Protected endpoints carry the requirement; /health stays public.
    assert spec["paths"]["/agents"]["get"].get("security")
    assert not spec["paths"]["/health"]["get"].get("security")
