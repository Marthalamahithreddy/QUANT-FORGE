"""Readiness endpoint tests.

The matrix of dependency states is driven through FastAPI dependency overrides,
so these tests are deterministic and never require PostgreSQL or Redis to be
running. The liveness and credential-leakage tests deliberately use the real
probes against an unreachable DSN, which fails instantly on loopback.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from quantforge.api.v1 import ready as ready_module
from quantforge.api.v1.ready import check_database, check_redis
from quantforge.core.config import Settings, get_settings
from quantforge.core.dependencies import DependencyCheck, DependencyName, DependencyState
from quantforge.main import app as module_level_app
from quantforge.main import create_app

READY_PATH = "/api/v1/health/ready"

#: Distinctive so a leak would be unmistakable in an assertion failure.
SECRET = "sup3rs3cret-do-not-leak"
UNREACHABLE_DSN = f"postgresql+psycopg://quantforge:{SECRET}@127.0.0.1:1/quantforge"

DATABASE_OK = DependencyCheck.ok(DependencyName.DATABASE)
DATABASE_DOWN = DependencyCheck.failure(DependencyName.DATABASE, ConnectionError("refused"))
REDIS_OK = DependencyCheck.ok(DependencyName.REDIS)
REDIS_DOWN = DependencyCheck.failure(DependencyName.REDIS, ConnectionError("refused"))


def _app_with(
    database: DependencyCheck,
    redis: DependencyCheck,
    settings: Settings,
) -> FastAPI:
    """Build an app whose probes and settings resolve to fixed values."""
    application = create_app(settings)
    application.dependency_overrides[get_settings] = lambda: settings
    application.dependency_overrides[check_database] = lambda: database
    application.dependency_overrides[check_redis] = lambda: redis
    return application


# ── Status and payload matrix ────────────────────────────────────────────────


def test_ready_when_both_dependencies_succeed(make_settings: Callable[..., Settings]) -> None:
    application = _app_with(DATABASE_OK, REDIS_OK, make_settings())

    with TestClient(application) as client:
        response = client.get(READY_PATH)

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "service": "quantforge-api",
        "dependencies": {"database": "ok", "redis": "ok"},
        "detail": None,
    }


def test_not_ready_when_postgres_fails(make_settings: Callable[..., Settings]) -> None:
    application = _app_with(DATABASE_DOWN, REDIS_OK, make_settings())

    with TestClient(application) as client:
        response = client.get(READY_PATH)

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "service": "quantforge-api",
        "dependencies": {"database": "unavailable", "redis": "ok"},
        "detail": {"database": "ConnectionError"},
    }


def test_not_ready_when_redis_fails(make_settings: Callable[..., Settings]) -> None:
    application = _app_with(DATABASE_OK, REDIS_DOWN, make_settings())

    with TestClient(application) as client:
        response = client.get(READY_PATH)

    assert response.status_code == 503
    payload = response.json()
    assert payload["dependencies"] == {"database": "ok", "redis": "unavailable"}
    assert payload["detail"] == {"redis": "ConnectionError"}


def test_not_ready_when_both_fail(make_settings: Callable[..., Settings]) -> None:
    application = _app_with(DATABASE_DOWN, REDIS_DOWN, make_settings())

    with TestClient(application) as client:
        response = client.get(READY_PATH)

    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "not_ready"
    assert payload["dependencies"] == {"database": "unavailable", "redis": "unavailable"}
    assert payload["detail"] == {"database": "ConnectionError", "redis": "ConnectionError"}


def test_dependencies_are_always_reported(make_settings: Callable[..., Settings]) -> None:
    """Both keys are present regardless of outcome, so clients can rely on them."""
    application = _app_with(DATABASE_DOWN, REDIS_OK, make_settings())

    with TestClient(application) as client:
        payload = client.get(READY_PATH).json()

    assert set(payload["dependencies"]) == {"database", "redis"}


def test_service_name_comes_from_settings(make_settings: Callable[..., Settings]) -> None:
    application = _app_with(DATABASE_OK, REDIS_OK, make_settings(app_name="custom-api"))

    with TestClient(application) as client:
        assert client.get(READY_PATH).json()["service"] == "custom-api"


# ── Dependency wiring ────────────────────────────────────────────────────────


def test_probes_delegate_settings_to_the_infrastructure_layer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The FastAPI wrappers must forward settings and return the probe result."""
    forwarded: list[Settings] = []

    def _fake_database(settings: Settings) -> DependencyCheck:
        forwarded.append(settings)
        return DATABASE_OK

    def _fake_redis(settings: Settings) -> DependencyCheck:
        forwarded.append(settings)
        return REDIS_DOWN

    monkeypatch.setattr(ready_module, "ping_database", _fake_database)
    monkeypatch.setattr(ready_module, "ping_redis", _fake_redis)
    settings = Settings(_env_file=None)

    assert check_database(settings) is DATABASE_OK
    assert check_redis(settings) is REDIS_DOWN
    assert forwarded == [settings, settings]


# ── Backward compatibility of the liveness endpoints ─────────────────────────


def test_health_stays_200_with_both_dependencies_down(
    make_settings: Callable[..., Settings],
) -> None:
    """Liveness is a process probe and must not care about datastores."""
    application = _app_with(DATABASE_DOWN, REDIS_DOWN, make_settings())

    with TestClient(application) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/api/v1/health").status_code == 200


def test_health_contract_is_unchanged(make_settings: Callable[..., Settings]) -> None:
    application = _app_with(DATABASE_OK, REDIS_OK, make_settings())

    with TestClient(application) as client:
        payload = client.get("/api/v1/health").json()

    assert set(payload) == {"status", "service", "version", "environment"}


def test_readiness_does_not_replace_the_liveness_path(
    make_settings: Callable[..., Settings],
) -> None:
    """Readiness is versioned only; /health/ready must not appear."""
    application = _app_with(DATABASE_OK, REDIS_OK, make_settings())

    with TestClient(application) as client:
        assert client.get("/health/ready").status_code == 404


def test_readiness_respects_a_custom_prefix(make_settings: Callable[..., Settings]) -> None:
    application = _app_with(
        DATABASE_OK,
        REDIS_OK,
        make_settings(api_v1_prefix="/api/v2"),
    )

    with TestClient(application) as client:
        assert client.get("/api/v2/health/ready").status_code == 200
        assert client.get(READY_PATH).status_code == 404


# ── Real probes against an unreachable database ──────────────────────────────


def _unreachable_app(settings: Settings) -> FastAPI:
    """An app with a genuinely broken DSN and a stubbed-out Redis probe."""
    application = create_app(settings)
    application.dependency_overrides[get_settings] = lambda: settings
    application.dependency_overrides[check_redis] = lambda: REDIS_OK
    return application


def test_real_database_failure_is_reported_cleanly(
    make_settings: Callable[..., Settings],
) -> None:
    """The genuine probe must return 503 rather than raising a 500."""
    app_under_test = _unreachable_app(make_settings(database_url=UNREACHABLE_DSN))

    with TestClient(app_under_test) as client:
        response = client.get(READY_PATH)

    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "not_ready"
    assert payload["dependencies"]["database"] == "unavailable"
    assert payload["detail"]["database"] == "OperationalError"


def test_health_survives_a_genuinely_broken_dsn(
    make_settings: Callable[..., Settings],
) -> None:
    """Proves liveness is independent of a real, failing connection attempt."""
    app_under_test = _unreachable_app(make_settings(database_url=UNREACHABLE_DSN))

    with TestClient(app_under_test) as client:
        assert client.get("/health").status_code == 200


def test_readiness_response_leaks_no_credentials(
    make_settings: Callable[..., Settings],
) -> None:
    """No password, DSN, or driver chatter may appear in the body."""
    app_under_test = _unreachable_app(make_settings(database_url=UNREACHABLE_DSN))

    with TestClient(app_under_test) as client:
        body = client.get(READY_PATH).text

    assert SECRET not in body
    assert "127.0.0.1" not in body
    assert "postgresql" not in body
    assert "psycopg" not in body
    assert "Traceback" not in body
    assert "quantforge:" not in body


def test_unavailable_detail_defaults_to_the_state_name(
    make_settings: Callable[..., Settings],
) -> None:
    """A failure with no exception type still yields a useful, safe label."""
    bare = DependencyCheck(name=DependencyName.REDIS, state=DependencyState.UNAVAILABLE)
    application = _app_with(DATABASE_OK, bare, make_settings())

    with TestClient(application) as client:
        payload = client.get(READY_PATH).json()

    assert payload["detail"] == {"redis": "unavailable"}


# ── OpenAPI ──────────────────────────────────────────────────────────────────


def test_openapi_documents_both_readiness_statuses(client: TestClient) -> None:
    responses = client.get("/openapi.json").json()["paths"][READY_PATH]["get"]["responses"]

    assert set(responses) >= {"200", "503"}


def test_openapi_does_not_add_an_unversioned_readiness(client: TestClient) -> None:
    assert "/health/ready" not in client.get("/openapi.json").json()["paths"]


def test_readiness_is_registered_on_the_module_level_app() -> None:
    paths = module_level_app.openapi()["paths"]
    assert READY_PATH in paths


def test_response_model_is_declared(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    responses = schema["paths"][READY_PATH]["get"]["responses"]

    assert responses["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ReadinessResponse"
    }


def test_dependency_states_are_constrained_in_the_schema(client: TestClient) -> None:
    properties: dict[str, Any] = client.get("/openapi.json").json()["components"]["schemas"][
        "DependencyReadiness"
    ]["properties"]

    for name in ("database", "redis"):
        assert set(properties[name]["enum"]) == {"ok", "unavailable"}
