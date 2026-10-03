"""Earnings API behaviour that needs no database."""

from __future__ import annotations

from collections.abc import Iterator

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
