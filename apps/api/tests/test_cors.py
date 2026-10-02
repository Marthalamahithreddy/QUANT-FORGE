"""CORS middleware behaviour tests.

These assert the policy actually applied to the running application, not just
the parsed settings.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi.testclient import TestClient

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
