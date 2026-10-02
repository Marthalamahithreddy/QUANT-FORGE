"""Health endpoint contract tests."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from quantforge.core.config import Settings

EXPECTED_KEYS = {"status", "service", "version", "environment"}
HEALTH_PATHS = ("/health", "/api/v1/health")


@pytest.mark.parametrize("path", HEALTH_PATHS)
def test_health_returns_200(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 200


@pytest.mark.parametrize("path", HEALTH_PATHS)
def test_health_payload_contract(client: TestClient, settings: Settings, path: str) -> None:
    payload: dict[str, Any] = client.get(path).json()

    assert set(payload) == EXPECTED_KEYS
    assert payload == {
        "status": "ok",
        "service": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
    }


@pytest.mark.parametrize("path", HEALTH_PATHS)
def test_health_reports_no_datastore_status(client: TestClient, path: str) -> None:
    """Backing datastores are not configured yet, so must not be reported."""
    payload = client.get(path).json()

    for forbidden in ("database", "postgres", "redis", "dependencies", "checks"):
        assert forbidden not in payload


def test_both_health_paths_return_identical_payloads(client: TestClient) -> None:
    assert client.get("/health").json() == client.get("/api/v1/health").json()


@pytest.mark.parametrize(
    ("environment", "expected"),
    [("local", "local"), ("development", "development"), ("staging", "staging")],
)
def test_health_reports_configured_environment(
    client_factory: Callable[..., TestClient],
    make_settings: Callable[..., Settings],
    environment: str,
    expected: str,
) -> None:
    with client_factory(make_settings(environment=environment)) as client:
        payload = client.get("/health").json()

    assert payload["environment"] == expected
    assert payload["service"] == "quantforge-api"


def test_unknown_route_returns_404(client: TestClient) -> None:
    assert client.get("/api/v1/nonexistent").status_code == 404


def test_openapi_documents_health_response_model(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    response = schema["paths"]["/api/v1/health"]["get"]["responses"]["200"]

    assert response["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/HealthResponse"
    }


def test_openapi_documents_both_health_paths(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]

    assert "/health" in paths
    assert "/api/v1/health" in paths


def test_health_response_schema_shape(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    properties = schema["components"]["schemas"]["HealthResponse"]["properties"]

    assert set(properties) == EXPECTED_KEYS
    assert properties["status"]["const"] == "ok"
