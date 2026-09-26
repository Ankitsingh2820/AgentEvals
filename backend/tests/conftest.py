"""Test fixtures: an in-memory SQLite database and a deterministic mock provider.

Tests never call a real LLM; the mock provider makes traces reproducible.
"""

import os

os.environ.setdefault("AGENTEVAL_DATABASE_URL", "sqlite://")
# Most tests exercise behaviour, not auth; tests/test_security.py switches auth back on.
os.environ.setdefault("AGENTEVAL_AUTH_ENABLED", "false")
os.environ.setdefault("AGENTEVAL_LLM_RETRY_BASE_SECONDS", "0")
os.environ.setdefault("AGENTEVAL_TASK_BACKEND", "inline")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401
from app.db import Base, get_db, get_session_factory
from app.main import app


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    from app.ratelimit import get_limiter

    get_limiter().reset()
    yield


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def db(session_factory):
    session = session_factory()
    yield session
    session.close()


@pytest.fixture
def client(db, session_factory):
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_session_factory] = lambda: session_factory
    yield TestClient(app)
    app.dependency_overrides.clear()


# CI stages (see .github/workflows/ci.yml): unit -> integration -> evaluation.
INTEGRATION_MODULES = {"test_security", "test_worker", "test_telemetry"}
EVALUATION_TESTS = {"test_regression_suite"}


def pytest_collection_modifyitems(items):
    for item in items:
        module = item.module.__name__.rsplit(".", 1)[-1]
        if item.name in EVALUATION_TESTS:
            item.add_marker(pytest.mark.evaluation)
        elif module.endswith("_api") or module in INTEGRATION_MODULES:
            item.add_marker(pytest.mark.integration)
