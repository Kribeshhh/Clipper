import os
from secrets import token_hex

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.settings import Settings, get_settings
from app.main import create_app


@pytest.fixture
def client(monkeypatch):
    for name in ("POSTGRES_PASSWORD", "REDIS_PASSWORD"):
        monkeypatch.setenv(name, token_hex(24))
    get_settings.cache_clear()
    with TestClient(create_app(), raise_server_exceptions=False) as test_client:
        yield test_client
    get_settings.cache_clear()


def test_liveness_without_dependencies(client):
    assert client.get("/api/health/live").json() == {"status": "ok"}


@pytest.mark.parametrize(
    "database, broker", [(True, True), (True, False), (False, True)]
)
def test_readiness(client, monkeypatch, database, broker):
    checks = {"postgresql": database, "redis": broker}
    monkeypatch.setattr(client.app.state.dependencies, "check", lambda: checks)
    response = client.get("/api/health/ready")
    ready = database and broker
    assert response.status_code == (200 if ready else 503)
    assert response.json() == {
        "status": "ready" if ready else "degraded",
        "checks": checks,
    }


def test_unknown_route(client):
    response = client.get("/missing")
    assert response.status_code == 404
    assert response.json() == {"error": {"code": "not_found"}}


def test_internal_failure(client, monkeypatch):
    def fail():
        raise RuntimeError("private detail")

    monkeypatch.setattr(client.app.state.dependencies, "check", fail)
    response = client.get("/api/health/ready")
    assert response.status_code == 500
    assert response.headers["x-request-id"]
    assert response.json() == {"error": {"code": "internal_error"}}


@pytest.mark.parametrize(
    "origin", ["*", "https://*.example.test", "https://a.test/path"]
)
def test_reject_invalid_origin(origin):
    with pytest.raises(ValidationError, match="explicit HTTP"):
        Settings(
            _env_file=None,
            postgres_password=token_hex(),
            redis_password=token_hex(),
            cors_origins=[origin],
        )


def test_require_secrets(monkeypatch):
    for name in ("POSTGRES_PASSWORD", "REDIS_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None)
    assert {e["loc"] for e in exc.value.errors()} >= {
        ("postgres_password",),
        ("redis_password",),
    }


@pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION") != "1", reason="requires PostgreSQL and Redis"
)
def test_real_dependencies():
    with TestClient(create_app()) as client:
        response = client.get("/api/health/ready")
        assert response.status_code == 200
        assert response.json()["checks"] == {"postgresql": True, "redis": True}
