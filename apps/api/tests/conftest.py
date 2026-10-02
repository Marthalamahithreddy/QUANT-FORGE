"""Shared pytest fixtures.

Every test runs with ``QF_*`` environment variables stripped and with ``.env``
files bypassed, so results never depend on a developer's shell or a stray file
on disk. Nothing here requires PostgreSQL or Redis to be running.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from quantforge.cache.redis import close_redis
from quantforge.core.config import Settings, get_settings
from quantforge.db.session import dispose_engines
from quantforge.main import create_app

ClientFactory = Callable[..., TestClient]


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove ambient ``QF_*`` variables so configuration tests are hermetic."""
    for key in tuple(os.environ):
        if key.startswith("QF_"):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture
def anyio_backend() -> str:
    """Run ``@pytest.mark.anyio`` tests on asyncio only.

    The adapter is asyncio-native (httpx, ``async with`` on the client), so
    running the suite against trio and curio as well would triple the async test
    time to re-verify a backend nothing in this project uses.
    """
    return "asyncio"


@pytest.fixture(autouse=True)
def _reset_engine_registry() -> Iterator[None]:
    """Guarantee no engine leaks between tests."""
    dispose_engines()
    yield
    dispose_engines()


@pytest.fixture(autouse=True)
def _reset_redis_registry() -> Iterator[None]:
    """Guarantee no Redis client leaks between tests."""
    close_redis()
    yield
    close_redis()


def make_app(settings: Settings) -> FastAPI:
    """Build an app whose route dependencies resolve to ``settings``."""
    application = create_app(settings)
    application.dependency_overrides[get_settings] = lambda: settings
    return application


@pytest.fixture
def settings() -> Settings:
    """Settings built purely from defaults, ignoring any ``.env`` file."""
    return Settings(_env_file=None)


@pytest.fixture
def make_settings() -> Callable[..., Settings]:
    """Factory for ``Settings`` instances that ignore any ``.env`` file."""

    def _factory(**overrides: object) -> Settings:
        return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]

    return _factory


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    """A FastAPI application bound to the default test settings."""
    return make_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    """A ``TestClient`` exercising the app's startup and shutdown events."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def client_factory() -> ClientFactory:
    """Build a ``TestClient`` for an explicitly configured application."""

    def _factory(settings: Settings) -> TestClient:
        return TestClient(make_app(settings))

    return _factory
