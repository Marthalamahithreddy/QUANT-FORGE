"""CORS middleware behaviour tests.

These assert the policy actually applied to the running application, not just
the parsed settings.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from quantforge.core.config import Settings

ALLOWED_ORIGIN = "https://quantforge.test"
FOREIGN_ORIGIN = "https://not-allowed.test"


def test_preflight_allows_configured_origin(
    client_factory: Callable[..., TestClient],
    make_settings: Callable[..., Settings],
) -> None:
    with client_factory(make_settings(cors_origins=[ALLOWED_ORIGIN])) as client:
        response = client.options(
            "/health",
            headers={
                "Origin": ALLOWED_ORIGIN,
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN


def test_preflight_omits_unconfigured_origin(
    client_factory: Callable[..., TestClient],
    make_settings: Callable[..., Settings],
) -> None:
    with client_factory(make_settings(cors_origins=[ALLOWED_ORIGIN])) as client:
        response = client.options(
            "/health",
            headers={
                "Origin": FOREIGN_ORIGIN,
                "Access-Control-Request-Method": "GET",
            },
        )

    assert "access-control-allow-origin" not in response.headers


def test_simple_request_echoes_allowed_origin(
    client_factory: Callable[..., TestClient],
    make_settings: Callable[..., Settings],
) -> None:
    with client_factory(make_settings(cors_origins=[ALLOWED_ORIGIN])) as client:
        response = client.get("/health", headers={"Origin": ALLOWED_ORIGIN})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN


def test_credentials_header_sent_when_enabled(
    client_factory: Callable[..., TestClient],
    make_settings: Callable[..., Settings],
) -> None:
    with client_factory(
        make_settings(cors_origins=[ALLOWED_ORIGIN], cors_allow_credentials=True)
    ) as client:
        response = client.get("/health", headers={"Origin": ALLOWED_ORIGIN})

    assert response.headers["access-control-allow-credentials"] == "true"


def test_credentials_header_absent_when_disabled(
    client_factory: Callable[..., TestClient],
    make_settings: Callable[..., Settings],
) -> None:
    with client_factory(
        make_settings(cors_origins=[ALLOWED_ORIGIN], cors_allow_credentials=False)
    ) as client:
        response = client.get("/health", headers={"Origin": ALLOWED_ORIGIN})

    assert "access-control-allow-credentials" not in response.headers


def test_default_app_does_not_allow_foreign_origin(client: TestClient) -> None:
    """The default policy is the local dev origin only, never a wildcard."""
    response = client.get("/health", headers={"Origin": FOREIGN_ORIGIN})

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_default_app_allows_localhost_3000(client: TestClient) -> None:
    response = client.get("/health", headers={"Origin": "http://localhost:3000"})

    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_v1_health_also_honours_cors(
    client_factory: Callable[..., TestClient],
    make_settings: Callable[..., Settings],
) -> None:
    with client_factory(make_settings(cors_origins=[ALLOWED_ORIGIN])) as client:
        response = client.get("/api/v1/health", headers={"Origin": ALLOWED_ORIGIN})

    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN


# ── Step 12D: the production policy ──────────────────────────────────────────────────────────

PRODUCTION = {
    "environment": "production",
    "database_url": "postgresql+psycopg://qf_app:prod-secret-not-real@db.internal:5432/quantforge",
}


def test_the_default_policy_is_read_only_and_credential_free(client: TestClient) -> None:
    response = client.options(
        "/health",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"},
    )

    assert response.status_code == 200
    allowed = {
        method.strip() for method in response.headers["access-control-allow-methods"].split(",")
    }
    assert allowed == {"GET", "OPTIONS"}
    assert "access-control-allow-credentials" not in response.headers


def test_a_write_method_is_not_advertised_and_a_write_preflight_is_refused(
    client: TestClient,
) -> None:
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        response = client.options(
            "/health",
            headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": method},
        )
        assert response.status_code == 400, method
        assert "Disallowed CORS method" in response.text


def test_production_allows_only_the_explicit_origin(
    client_factory: Callable[..., TestClient], make_settings: Callable[..., Settings]
) -> None:
    settings = make_settings(**PRODUCTION, cors_origins=[ALLOWED_ORIGIN])
    with client_factory(settings) as client:
        allowed = client.get("/health", headers={"Origin": ALLOWED_ORIGIN})
        foreign = client.get("/health", headers={"Origin": FOREIGN_ORIGIN})
        localhost = client.get("/health", headers={"Origin": "http://localhost:3000"})

    assert allowed.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert "access-control-allow-credentials" not in allowed.headers
    assert "access-control-allow-origin" not in foreign.headers
    assert "access-control-allow-origin" not in localhost.headers, "localhost is not inherited"


def test_production_without_explicit_origins_cannot_start(
    make_settings: Callable[..., Settings],
) -> None:
    with pytest.raises(ValidationError, match="QF_CORS_ORIGINS"):
        make_settings(**PRODUCTION)


def test_credentials_are_an_explicit_opt_in_even_in_production(
    client_factory: Callable[..., TestClient], make_settings: Callable[..., Settings]
) -> None:
    settings = make_settings(
        **PRODUCTION, cors_origins=[ALLOWED_ORIGIN], cors_allow_credentials=True
    )
    with client_factory(settings) as client:
        response = client.get("/health", headers={"Origin": ALLOWED_ORIGIN})
    assert response.headers["access-control-allow-credentials"] == "true"
