"""Readiness endpoint tests.

Readiness is ready only when PostgreSQL answers AND its schema is exactly this binary's migration
head; Redis is reported and never gates (12D). The matrix of states is driven through FastAPI
dependency overrides, so these tests are deterministic and never require PostgreSQL or Redis. The
credential-leakage and real-failure tests deliberately use the real probes against an unreachable
DSN, which fails instantly on loopback. Behind/ahead/missing schemas against a real database live in
``tests/integration/test_readiness_schema.py``.
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
from quantforge.core.dependencies import (
    DatabaseProbe,
    DependencyCheck,
    DependencyName,
    DependencyState,
    SchemaCheck,
    SchemaState,
)
from quantforge.db.schema import expected_head
from quantforge.main import app as module_level_app
from quantforge.main import create_app

READY_PATH = "/api/v1/health/ready"

#: Distinctive so a leak would be unmistakable in an assertion failure.
SECRET = "sup3rs3cret-do-not-leak"
UNREACHABLE_DSN = f"postgresql+psycopg://quantforge:{SECRET}@127.0.0.1:1/quantforge"

HEAD = expected_head()
assert HEAD is not None, "the repository must have exactly one migration head"

DATABASE_OK = DependencyCheck.ok(DependencyName.DATABASE)
DATABASE_DOWN = DependencyCheck.failure(DependencyName.DATABASE, ConnectionError("refused"))
SCHEMA_OK = SchemaCheck(SchemaState.COMPATIBLE, expected=HEAD, current=HEAD)
SCHEMA_BEHIND = SchemaCheck(SchemaState.MISMATCH, expected=HEAD, current="0013")
SCHEMA_AHEAD = SchemaCheck(SchemaState.MISMATCH, expected=HEAD, current="9999")
SCHEMA_UNKNOWN = SchemaCheck(SchemaState.UNKNOWN, expected=HEAD, current=None)

PROBE_OK = DatabaseProbe(DATABASE_OK, SCHEMA_OK)
REDIS_OK = DependencyCheck.ok(DependencyName.REDIS)
REDIS_DOWN = DependencyCheck.failure(DependencyName.REDIS, ConnectionError("refused"))
REDIS_NOT_CONFIGURED = DependencyCheck(DependencyName.REDIS, DependencyState.NOT_CONFIGURED)


def _app_with(
    database: DatabaseProbe,
    redis: DependencyCheck,
    settings: Settings,
) -> FastAPI:
    """Build an app whose probes and settings resolve to fixed values."""
    application = create_app(settings)
    application.dependency_overrides[get_settings] = lambda: settings
    application.dependency_overrides[check_database] = lambda: database
    application.dependency_overrides[check_redis] = lambda: redis
    return application


def _get(
    settings: Settings,
    database: DatabaseProbe = PROBE_OK,
    redis: DependencyCheck = REDIS_OK,
) -> tuple[int, dict[str, Any]]:
    with TestClient(_app_with(database, redis, settings)) as client:
        response = client.get(READY_PATH)
    return response.status_code, response.json()


# ── Status and payload matrix ────────────────────────────────────────────────


def test_ready_when_database_schema_and_redis_are_all_healthy(
    make_settings: Callable[..., Settings],
) -> None:
    code, payload = _get(make_settings())

    assert code == 200
    assert payload == {
        "status": "ready",
        "service": "quantforge-api",
        "dependencies": {
            "database": {"state": "ok", "gating": True},
            "redis": {"state": "ok", "gating": False},
        },
        "schema": {"state": "compatible", "expected": HEAD, "current": HEAD, "gating": True},
        "detail": None,
    }


def test_redis_unavailable_does_not_make_the_service_not_ready(
    make_settings: Callable[..., Settings],
) -> None:
    """Redis is non-gating: nothing that serves the persisted product uses it."""
    code, payload = _get(make_settings(), redis=REDIS_DOWN)

    assert code == 200
    assert payload["status"] == "ready"
    assert payload["dependencies"]["redis"] == {"state": "unavailable", "gating": False}
    assert payload["dependencies"]["database"]["state"] == "ok"
    assert payload["detail"] == {"redis": "ConnectionError"}


def test_redis_not_configured_is_its_own_state_and_not_an_outage(
    make_settings: Callable[..., Settings],
) -> None:
    code, payload = _get(make_settings(), redis=REDIS_NOT_CONFIGURED)

    assert code == 200 and payload["status"] == "ready"
    assert payload["dependencies"]["redis"] == {"state": "not_configured", "gating": False}
    assert payload["detail"] is None, "not an outage, so nothing to explain"


def test_not_ready_when_the_database_is_unreachable(
    make_settings: Callable[..., Settings],
) -> None:
    unreachable = DatabaseProbe(DATABASE_DOWN, SchemaCheck(SchemaState.UNKNOWN, expected=HEAD))
    code, payload = _get(make_settings(), database=unreachable)

    assert code == 503
    assert payload["status"] == "not_ready"
    assert payload["dependencies"]["database"] == {"state": "unavailable", "gating": True}
    assert payload["schema"] == {
        "state": "unknown",
        "expected": HEAD,
        "current": None,
        "gating": True,
    }
    assert payload["detail"] == {"database": "ConnectionError"}


@pytest.mark.parametrize(
    ("label", "schema", "state", "current"),
    [
        ("behind", SCHEMA_BEHIND, "mismatch", "0013"),
        ("ahead of the binary", SCHEMA_AHEAD, "mismatch", "9999"),
        ("version missing or ambiguous", SCHEMA_UNKNOWN, "unknown", None),
    ],
)
def test_not_ready_for_any_schema_that_is_not_exactly_the_expected_head(
    make_settings: Callable[..., Settings],
    label: str,
    schema: SchemaCheck,
    state: str,
    current: str | None,
) -> None:
    code, payload = _get(make_settings(), database=DatabaseProbe(DATABASE_OK, schema))

    assert code == 503, label
    assert payload["status"] == "not_ready", label
    assert payload["dependencies"]["database"]["state"] == "ok", "the database itself is fine"
    assert payload["schema"] == {
        "state": state,
        "expected": HEAD,
        "current": current,
        "gating": True,
    }
    assert payload["detail"] is None, "a schema problem is not an unavailable dependency"


def test_redis_down_cannot_rescue_or_condemn_a_schema_failure(
    make_settings: Callable[..., Settings],
) -> None:
    code, payload = _get(
        make_settings(), database=DatabaseProbe(DATABASE_OK, SCHEMA_BEHIND), redis=REDIS_DOWN
    )
    assert code == 503 and payload["status"] == "not_ready"
    assert payload["dependencies"]["redis"]["state"] == "unavailable"


def test_both_failing_reports_both(make_settings: Callable[..., Settings]) -> None:
    unreachable = DatabaseProbe(DATABASE_DOWN, SchemaCheck(SchemaState.UNKNOWN, expected=HEAD))
    code, payload = _get(make_settings(), database=unreachable, redis=REDIS_DOWN)

    assert code == 503
    assert payload["detail"] == {"database": "ConnectionError", "redis": "ConnectionError"}


def test_every_component_is_always_reported(make_settings: Callable[..., Settings]) -> None:
    """The keys a client can rely on exist whatever the outcome."""
    _, payload = _get(make_settings(), database=DatabaseProbe(DATABASE_DOWN, SCHEMA_UNKNOWN))

    assert set(payload) == {"status", "service", "dependencies", "schema", "detail"}
    assert set(payload["dependencies"]) == {"database", "redis"}
    assert set(payload["schema"]) == {"state", "expected", "current", "gating"}


def test_the_gating_policy_is_part_of_the_contract(make_settings: Callable[..., Settings]) -> None:
    """Which components gate readiness is machine-readable, and it is constant."""
    for redis in (REDIS_OK, REDIS_DOWN, REDIS_NOT_CONFIGURED):
        _, payload = _get(make_settings(), redis=redis)
        assert payload["dependencies"]["database"]["gating"] is True
        assert payload["schema"]["gating"] is True
        assert payload["dependencies"]["redis"]["gating"] is False


def test_service_name_comes_from_settings(make_settings: Callable[..., Settings]) -> None:
    _, payload = _get(make_settings(app_name="custom-api"))
    assert payload["service"] == "custom-api"


# ── Dependency wiring ────────────────────────────────────────────────────────


def test_probes_delegate_settings_to_the_infrastructure_layer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The FastAPI wrappers must forward settings and return the probe result."""
    forwarded: list[Settings] = []

    def _fake_database(settings: Settings) -> DatabaseProbe:
        forwarded.append(settings)
        return PROBE_OK

    def _fake_redis(settings: Settings) -> DependencyCheck:
        forwarded.append(settings)
        return REDIS_DOWN

    monkeypatch.setattr(ready_module, "probe_database", _fake_database)
    monkeypatch.setattr(ready_module, "ping_redis", _fake_redis)
    settings = Settings(_env_file=None)

    assert check_database(settings) is PROBE_OK
    assert check_redis(settings) is REDIS_DOWN
    assert forwarded == [settings, settings]


# ── Backward compatibility of the liveness endpoints ─────────────────────────


def test_health_stays_200_with_both_dependencies_down(
    make_settings: Callable[..., Settings],
) -> None:
    """Liveness is a process probe and must not care about datastores."""
    application = _app_with(
        DatabaseProbe(DATABASE_DOWN, SCHEMA_UNKNOWN), REDIS_DOWN, make_settings()
    )

    with TestClient(application) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/api/v1/health").status_code == 200


def test_health_contract_is_unchanged(make_settings: Callable[..., Settings]) -> None:
    application = _app_with(PROBE_OK, REDIS_OK, make_settings())

    with TestClient(application) as client:
        payload = client.get("/api/v1/health").json()

    assert set(payload) == {"status", "service", "version", "environment"}


def test_readiness_does_not_replace_the_liveness_path(
    make_settings: Callable[..., Settings],
) -> None:
    """Readiness is versioned only; /health/ready must not appear."""
    application = _app_with(PROBE_OK, REDIS_OK, make_settings())

    with TestClient(application) as client:
        assert client.get("/health/ready").status_code == 404


def test_readiness_respects_a_custom_prefix(make_settings: Callable[..., Settings]) -> None:
    application = _app_with(PROBE_OK, REDIS_OK, make_settings(api_v1_prefix="/api/v2"))

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
    assert payload["dependencies"]["database"] == {"state": "unavailable", "gating": True}
    assert payload["schema"]["state"] == "unknown"
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
    application = _app_with(PROBE_OK, bare, make_settings())

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


def test_component_states_are_constrained_in_the_schema(client: TestClient) -> None:
    schemas: dict[str, Any] = client.get("/openapi.json").json()["components"]["schemas"]

    assert set(schemas["DatabaseReadiness"]["properties"]["state"]["enum"]) == {"ok", "unavailable"}
    assert set(schemas["RedisReadiness"]["properties"]["state"]["enum"]) == {
        "ok",
        "unavailable",
        "not_configured",
    }
    assert set(schemas["SchemaReadiness"]["properties"]["state"]["enum"]) == {
        "compatible",
        "mismatch",
        "unknown",
    }
    assert schemas["RedisReadiness"]["properties"]["gating"]["const"] is False
    assert schemas["DatabaseReadiness"]["properties"]["gating"]["const"] is True
    assert schemas["SchemaReadiness"]["properties"]["gating"]["const"] is True
    assert "schema" in schemas["ReadinessResponse"]["properties"]
