"""Earnings API behaviour that needs no database."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from quantforge.core.config import Settings
from quantforge.db.session import get_session
from quantforge.main import create_app

SECRET = "sup3rs3cret-do-not-leak"


class _FailingSession:
    def scalar(self, *_: object, **__: object) -> None:
        raise OperationalError("SELECT", {}, Exception(f"password={SECRET}"))

    scalars = get = scalar


def test_database_failure_is_a_generic_503_without_leaks() -> None:
    application: FastAPI = create_app(Settings(_env_file=None))

    def _session() -> Iterator[_FailingSession]:
        yield _FailingSession()

    application.dependency_overrides[get_session] = _session
    with TestClient(application) as client:
        for path in (
            "/api/v1/earnings-events",
            "/api/v1/earnings-events/" + "0" * 8 + "-0000-0000-0000-" + "0" * 12,
        ):
            response = client.get(path)
            assert response.status_code == 503
            assert response.json() == {"detail": "Database unavailable."}
            assert SECRET not in response.text


_EVENT = "/api/v1/earnings-events/" + "0" * 8 + "-0000-0000-0000-" + "0" * 12


def _client_whose_database_must_not_be_touched() -> TestClient:
    application: FastAPI = create_app(Settings(_env_file=None))

    def _session() -> Iterator[_FailingSession]:
        yield _FailingSession()

    application.dependency_overrides[get_session] = _session
    return TestClient(application)


@pytest.mark.parametrize(
    "query",
    [
        "ticker=NKE&ticker=AAPL",
        "limit=5&limit=6",
        "offset=0&offset=1",
        "date_from=2026-01-01&date_from=2026-02-01",
        "date_to=2026-01-01&date_to=2026-02-01",
    ],
)
def test_the_list_rejects_every_repeated_scalar_parameter(query: str) -> None:
    """Last-value-wins would silently query AAPL for ``ticker=NKE&ticker=AAPL``."""
    with _client_whose_database_must_not_be_touched() as client:
        response = client.get(f"/api/v1/earnings-events?{query}")
    assert response.status_code == 422
    errors = response.json()["detail"]
    assert errors[0]["type"] == "repeated_parameter"
    assert errors[0]["loc"][0] == "query"


@pytest.mark.parametrize("query", ["ticker=NKE&ticker=AAPL", "x=1&x=2"])
def test_the_detail_rejects_repeated_parameters(query: str) -> None:
    with _client_whose_database_must_not_be_touched() as client:
        response = client.get(f"{_EVENT}?{query}")
    assert response.status_code == 422


@pytest.mark.parametrize("query", ["ticker=NKE", "limit=5", "unknown=1"])
def test_the_detail_rejects_every_query_parameter(query: str) -> None:
    """The detail route takes no query parameters; one is an error, never silently ignored."""
    with _client_whose_database_must_not_be_touched() as client:
        response = client.get(f"{_EVENT}?{query}")
    assert response.status_code == 422


def test_the_list_still_rejects_unknown_parameters() -> None:
    with _client_whose_database_must_not_be_touched() as client:
        assert client.get("/api/v1/earnings-events?order=desc").status_code == 422


def test_both_endpoints_document_the_generic_503_in_openapi() -> None:
    schema = create_app(Settings(_env_file=None)).openapi()
    for path, operation in (
        ("/api/v1/earnings-events", "get"),
        ("/api/v1/earnings-events/{event_id}", "get"),
    ):
        responses = schema["paths"][path][operation]["responses"]
        assert "503" in responses, path
        assert responses["503"]["description"] == "The database is unavailable."
    assert "404" in schema["paths"]["/api/v1/earnings-events/{event_id}"]["get"]["responses"]


@pytest.mark.parametrize("path", ["/api/v1/earnings-events", _EVENT])
def test_database_outage_body_is_generic_and_leaks_nothing(path: str) -> None:
    with _client_whose_database_must_not_be_touched() as client:
        response = client.get(path)
    assert response.status_code == 503
    assert response.json() == {"detail": "Database unavailable."}
    for leaked in (SECRET, "password", "SELECT", "OperationalError", "postgresql", "Traceback"):
        assert leaked not in response.text


@pytest.mark.parametrize("ticker", ["%00", "NK%00E", "NKE%00"])
def test_a_nul_byte_in_the_ticker_is_a_422_not_a_database_error(ticker: str) -> None:
    """PostgreSQL cannot store or compare a NUL; it must be refused before any query (was a 500)."""
    with _client_whose_database_must_not_be_touched() as client:
        response = client.get(f"/api/v1/earnings-events?ticker={ticker}")
    assert response.status_code == 422
