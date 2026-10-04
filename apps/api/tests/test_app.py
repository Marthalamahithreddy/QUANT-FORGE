"""Application factory tests."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from quantforge.core.config import Settings
from quantforge.main import app as module_level_app
from quantforge.main import create_app


def test_create_app_returns_fastapi(make_settings: Callable[..., Settings]) -> None:
    assert isinstance(create_app(make_settings()), FastAPI)


def test_module_level_app_exists_for_asgi_servers() -> None:
    """``uvicorn quantforge.main:app`` must resolve."""
    assert isinstance(module_level_app, FastAPI)


def test_app_metadata_comes_from_settings(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings(app_version="9.9.9")
    application = create_app(settings)

    assert application.version == "9.9.9"
    assert application.title == "QuantForge API"
    assert application.debug is settings.debug


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_interactive_docs_enabled_outside_production(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 200


def test_docs_disabled_in_production(
    client_factory: Callable[..., TestClient],
    make_settings: Callable[..., Settings],
) -> None:
    with client_factory(make_settings(environment="production")) as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/redoc").status_code == 404
        assert client.get("/openapi.json").status_code == 404


def test_health_still_serves_in_production(
    client_factory: Callable[..., TestClient],
    make_settings: Callable[..., Settings],
) -> None:
    with client_factory(make_settings(environment="production")) as client:
        assert client.get("/health").status_code == 200


def test_api_v1_prefix_is_configurable(
    client_factory: Callable[..., TestClient],
    make_settings: Callable[..., Settings],
) -> None:
    with client_factory(make_settings(api_v1_prefix="/api/v2")) as client:
        assert client.get("/api/v2/health").status_code == 200
        assert client.get("/api/v1/health").status_code == 404
        # The unversioned probe is unaffected by the version prefix.
        assert client.get("/health").status_code == 200


def find_cors_middleware(application: FastAPI) -> Any:
    """Return the installed ``CORSMiddleware`` entry, asserting it exists.

    Starlette types ``user_middleware`` entries loosely, so this narrows once
    here instead of scattering casts across the assertions below.
    """
    for entry in cast("list[Any]", application.user_middleware):
        if entry.cls is CORSMiddleware:
            return entry
    raise AssertionError("CORSMiddleware is not installed")


def test_cors_middleware_is_installed(make_settings: Callable[..., Settings]) -> None:
    application = create_app(make_settings())

    assert find_cors_middleware(application) is not None


def test_installed_cors_policy_has_no_wildcard(
    make_settings: Callable[..., Settings],
) -> None:
    application = create_app(make_settings(cors_origins=["https://quantforge.test"]))
    origins = find_cors_middleware(application).kwargs["allow_origins"]

    assert origins == ["https://quantforge.test"]
    assert "*" not in origins


def test_installed_cors_policy_uses_settings_origins(
    make_settings: Callable[..., Settings],
) -> None:
    allowed = ["https://a.test", "https://b.test"]
    application = create_app(make_settings(cors_origins=allowed))

    assert find_cors_middleware(application).kwargs["allow_origins"] == allowed


def test_openapi_declares_both_health_operations(app: FastAPI) -> None:
    paths = app.openapi()["paths"]

    assert "/health" in paths
    assert "/api/v1/health" in paths


# ── Step 11.0A: startup requirements are the API's own, not the ingestion providers' ──────


def test_the_api_starts_and_serves_without_any_provider_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "QF_MASSIVE_API_KEY",
        "QF_FINNHUB_API_KEY",
        "QF_SEC_USER_AGENT",
        "QF_MASSIVE_BASE_URL",
        "QF_SEC_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(_env_file=None)
    assert settings.massive_api_key is None and settings.sec_user_agent is None
    application = create_app(settings)
    assert TestClient(application).get("/health").status_code == 200
    assert "/api/v1/research/records" in application.openapi()["paths"]


@pytest.mark.parametrize(
    "database_url",
    ["user:dbpw-9f3a@host/db", "dbpw-9f3a", "mysql://user:dbpw-9f3a@host/db", ""],
)
def test_a_malformed_or_non_postgres_database_url_fails_at_startup_without_echoing_it(
    database_url: str,
) -> None:
    with pytest.raises(ValueError, match="QF_DATABASE_URL") as raised:
        create_app(Settings(_env_file=None, database_url=database_url))
    assert "dbpw-9f3a" not in str(raised.value)


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql://u:p@h/db",
        "postgres://u:p@h/db",
        "postgresql+psycopg://u:p@h/db",
    ],
)
def test_valid_postgres_urls_are_accepted_at_startup_with_no_connection_made(
    database_url: str,
) -> None:
    assert isinstance(create_app(Settings(_env_file=None, database_url=database_url)), FastAPI)
