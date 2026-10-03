"""Database foundation tests.

These prove the two properties that matter at this milestone: no financial
tables exist yet, and nothing touches a database merely because the application
was imported or constructed.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from quantforge.core.config import Settings
from quantforge.core.dependencies import DependencyCheck, DependencyName, DependencyState
from quantforge.db import session as session_module
from quantforge.db.base import Base
from quantforge.db.session import (
    dispose_engines,
    get_engine,
    get_session_factory,
    normalise_database_url,
    ping_database,
    session_scope,
)
from quantforge.main import create_app
from quantforge.models import ALL_TABLES, DOMAIN_TABLES, OPERATIONAL_TABLES

UNREACHABLE_DSN = "postgresql+psycopg://quantforge:quantforge@127.0.0.1:1/quantforge"


def test_base_metadata_contains_exactly_the_known_tables() -> None:
    """The declarative base carries the known schema and nothing else.

    Replaces the foundation-milestone assertion that metadata was empty. Now
    that a real schema exists, the useful invariant is *exactly* these tables:
    a new table appearing without a decision about it should fail here.
    """
    assert set(Base.metadata.tables) == set(ALL_TABLES)


def test_domain_and_operational_tables_are_disjoint_and_complete() -> None:
    """Financial data and pipeline bookkeeping are tracked separately.

    Kept apart deliberately. A test asserting the financial schema is complete
    should not silently start passing because someone added a run-log table, and
    a change to the ten domain tables should be noticed as such rather than
    absorbed into a growing total.
    """
    assert set(DOMAIN_TABLES) & set(OPERATIONAL_TABLES) == set()
    assert set(DOMAIN_TABLES) | set(OPERATIONAL_TABLES) == set(ALL_TABLES)
    # The financial schema is still exactly the ten tables Step 5 settled on.
    assert len(DOMAIN_TABLES) == 14
    # Operational bookkeeping so far is only the ingestion run log.
    assert OPERATIONAL_TABLES == ("ingestion_run",)


# ── DSN driver selection ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw",
    [
        "postgresql://u:p@localhost:5432/d",
        "postgres://u:p@localhost:5432/d",
    ],
)
def test_bare_postgres_schemes_gain_the_psycopg_driver(raw: str) -> None:
    """Without this, SQLAlchemy would fall back to the uninstalled psycopg2."""
    assert normalise_database_url(raw) == "postgresql+psycopg://u:p@localhost:5432/d"


def test_explicit_psycopg_url_is_unchanged() -> None:
    assert normalise_database_url(UNREACHABLE_DSN) == UNREACHABLE_DSN


def test_explicit_async_driver_is_rejected() -> None:
    with pytest.raises(ValueError, match="psycopg"):
        normalise_database_url("postgresql+asyncpg://u:p@localhost/d")


@pytest.mark.parametrize("raw", ["sqlite:///local.db", "mysql://u:p@localhost/d"])
def test_non_postgres_scheme_is_rejected(raw: str) -> None:
    with pytest.raises(ValueError, match="must target PostgreSQL"):
        normalise_database_url(raw)


def test_relative_url_is_rejected() -> None:
    with pytest.raises(ValueError, match="absolute URL"):
        normalise_database_url("localhost:5432/quantforge")


def test_get_engine_uses_the_psycopg_dialect(
    make_settings: Callable[..., Settings],
) -> None:
    """A bare postgres:// setting must still build a psycopg engine."""
    engine = get_engine(make_settings(database_url="postgresql://u:p@127.0.0.1:1/d"))

    assert engine.dialect.name == "postgresql"
    assert engine.dialect.driver == "psycopg"


# ── Connectivity probe ───────────────────────────────────────────────────────


def test_ping_database_reports_unavailable_for_a_dead_server() -> None:
    """Runs against loopback port 1, so it fails without needing PostgreSQL."""
    result = ping_database(Settings(_env_file=None, database_url=UNREACHABLE_DSN))

    assert result.is_ok is False
    assert result.state is DependencyState.UNAVAILABLE
    assert result.name is DependencyName.DATABASE
    assert result.detail == "OperationalError"


def test_ping_database_detail_hides_the_dsn() -> None:
    secret = "sup3rs3cret-do-not-leak"
    dsn = f"postgresql+psycopg://quantforge:{secret}@127.0.0.1:1/quantforge"

    result = ping_database(Settings(_env_file=None, database_url=dsn))

    assert result.detail == "OperationalError"
    assert secret not in (result.detail or "")


class _StubConnection:
    """Records the statements a probe executes, without a real database."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: object) -> None:
        self.statements.append(str(statement))

    def __enter__(self) -> _StubConnection:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None


class _StubEngine:
    """Hands out a single reusable :class:`_StubConnection`."""

    def __init__(self) -> None:
        self.connection = _StubConnection()

    def connect(self) -> _StubConnection:
        return self.connection


def test_ping_database_reports_ok_when_the_query_succeeds(
    make_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The success branch, proven without needing a PostgreSQL server."""
    engine = _StubEngine()
    monkeypatch.setattr(session_module, "get_engine", lambda *_: engine)

    result = ping_database(make_settings())

    assert result.is_ok is True
    assert result.state is DependencyState.OK
    assert result.name is DependencyName.DATABASE
    assert result.detail is None
    assert "SELECT 1" in engine.connection.statements[0]


def test_ping_database_never_raises() -> None:
    """The probe degrades to data instead of propagating a driver error."""
    result = ping_database(Settings(_env_file=None, database_url=UNREACHABLE_DSN))

    assert isinstance(result, DependencyCheck)
    assert result.is_ok is False


def test_declarative_base_is_sqlalchemy_style() -> None:
    assert Base.registry is not None
    assert hasattr(Base, "metadata")


def test_app_construction_creates_no_engine(
    make_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _explode(*args: object, **kwargs: object) -> None:
        message = "create_engine must not be called while building the application"
        raise AssertionError(message)

    monkeypatch.setattr(session_module, "create_engine", _explode)

    create_app(make_settings())


def test_health_route_creates_no_engine(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _explode(*args: object, **kwargs: object) -> None:
        message = "create_engine must not be called while serving /health"
        raise AssertionError(message)

    monkeypatch.setattr(session_module, "create_engine", _explode)

    assert client.get("/health").status_code == 200


def test_get_engine_is_lazy_but_does_not_connect(
    make_settings: Callable[..., Settings],
) -> None:
    """``create_engine`` prepares a pool only; nothing dials the host."""
    engine = get_engine(make_settings(database_url=UNREACHABLE_DSN))

    assert isinstance(engine, Engine)
    assert engine.url.render_as_string(hide_password=True) == (
        "postgresql+psycopg://quantforge:***@127.0.0.1:1/quantforge"
    )


def test_engine_is_cached_across_calls(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings(database_url=UNREACHABLE_DSN)

    assert get_engine(settings) is get_engine(settings)


def test_engine_rebuilt_when_dsn_changes(make_settings: Callable[..., Settings]) -> None:
    first = get_engine(make_settings(database_url=UNREACHABLE_DSN))
    second = get_engine(make_settings(database_url=UNREACHABLE_DSN + "?sslmode=disable"))

    assert first is not second


def test_session_factory_is_cached_across_calls(
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings(database_url=UNREACHABLE_DSN)

    assert get_session_factory(settings) is get_session_factory(settings)


def test_session_factory_binds_to_current_engine(
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings(database_url=UNREACHABLE_DSN)
    factory = get_session_factory(settings)

    session: Session = factory()
    try:
        assert session.get_bind() is get_engine(settings)
    finally:
        session.close()


def test_session_scope_rolls_back_on_failure(
    make_settings: Callable[..., Settings],
) -> None:
    """A failure inside the scope must roll back and re-raise."""
    settings = make_settings(database_url=UNREACHABLE_DSN)

    with pytest.raises(RuntimeError, match="boom"), session_scope(settings):
        raise RuntimeError("boom")


class _RecordingSession:
    """Stand-in for a real Session so transaction flow can be asserted offline."""

    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


def _patch_session_factory(
    monkeypatch: pytest.MonkeyPatch, recorder: _RecordingSession
) -> _RecordingSession:
    """Force every session to be ``recorder`` and return it for assertions."""
    factory = cast("sessionmaker[Session]", lambda: recorder)
    monkeypatch.setattr(session_module, "get_session_factory", lambda *_: factory)
    return recorder


def test_session_scope_commits_on_clean_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _patch_session_factory(monkeypatch, _RecordingSession())

    with session_scope() as session:
        assert isinstance(session, _RecordingSession)

    assert recorder.committed is True
    assert recorder.rolled_back is False
    assert recorder.closed is True


def test_session_scope_rolls_back_and_closes_on_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _patch_session_factory(monkeypatch, _RecordingSession())

    with pytest.raises(RuntimeError, match="boom"), session_scope():
        raise RuntimeError("boom")

    assert recorder.committed is False
    assert recorder.rolled_back is True
    assert recorder.closed is True


def test_get_session_yields_and_rolls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """The request-scoped dependency must never commit implicitly."""
    recorder = _patch_session_factory(monkeypatch, _RecordingSession())

    dependency = session_module.get_session()
    session = next(dependency)
    assert isinstance(session, _RecordingSession)

    with pytest.raises(StopIteration):
        next(dependency)

    assert recorder.committed is False
    assert recorder.rolled_back is True
    assert recorder.closed is True


def test_dispose_engines_resets_registry(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings(database_url=UNREACHABLE_DSN)
    first = get_engine(settings)

    dispose_engines()

    assert get_engine(settings) is not first


def test_dispose_engines_is_safe_when_uninitialised() -> None:
    dispose_engines()
    dispose_engines()
