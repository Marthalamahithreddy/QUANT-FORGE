"""Fixtures for the PostgreSQL integration layer.

These tests deliberately run against the real local PostgreSQL container, never
SQLite. The schema uses behaviour SQLite does not reproduce faithfully:

* partial unique indexes (``WHERE valid_to IS NULL``),
* ``CHECK`` constraints using the PostgreSQL regex operator ``~``,
* native ``UUID`` columns and ``TIMESTAMP WITH TIME ZONE``,
* ``NUMERIC`` round-tripping as exact ``Decimal``,
* and a half-open interval lookup whose boundary semantics depend on real
  ``DATE`` comparison.

Running them on SQLite would let all of that pass while the production database
rejects the data.

Isolation strategy
------------------
A dedicated ``quantforge_test`` database is created once per session and brought
to ``head`` by running the real Alembic migrations -- so these tests also verify
that the migration produces a working schema, not just that the ORM metadata
does. Each test then runs inside a SAVEPOINT on a shared connection whose outer
transaction is rolled back afterwards. That gives real constraint enforcement
(real PostgreSQL, real DDL, real indexes) with per-test isolation and no
truncation pass.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine
from sqlalchemy.orm import Session

API_ROOT = Path(__file__).resolve().parents[2]

#: Database the development container exposes. Overridable so the suite can point
#: at any local PostgreSQL without editing code.
DEFAULT_ADMIN_URL = "postgresql+psycopg://quantforge:quantforge@127.0.0.1:5432/postgres"
TEST_DB_NAME = "quantforge_test"


def _admin_url() -> str:
    """Return the DSN used to create and drop the test database.

    Read from ``QF_TEST_DATABASE_ADMIN_URL`` when present, otherwise from
    ``QF_DATABASE_URL`` with the database component swapped for ``postgres``,
    otherwise a compiled-in local default. Deriving it means the suite follows
    whatever the developer already configured instead of hard-coding a second
    credential.
    """
    override = os.environ.get("QF_TEST_DATABASE_ADMIN_URL")
    if override:
        return override
    app_url = os.environ.get("QF_DATABASE_URL")
    if app_url:
        parts = urlsplit(app_url)
        return urlunsplit((parts.scheme, parts.netloc, "/postgres", "", ""))
    return DEFAULT_ADMIN_URL


def _test_url(admin_url: str) -> str:
    """Return the DSN for the dedicated test database."""
    parts = urlsplit(admin_url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{TEST_DB_NAME}", "", ""))


def _admin_statements(admin_url: str, statements: list[str], **params: str) -> None:
    """Run maintenance ``statements`` against ``admin_url`` in autocommit mode.

    ``CREATE``/``DROP DATABASE`` cannot run inside a transaction, hence
    ``isolation_level="AUTOCOMMIT"``. The engine is explicitly disposed in a
    ``finally``: the suite runs with ``filterwarnings = ["error"]``, and an
    undisposed engine leaves a pooled ``psycopg`` connection to be garbage
    collected, which raises ``ResourceWarning`` and fails unrelated tests.
    """
    engine = sa.create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            for statement in statements:
                connection.execute(sa.text(statement), params)
    finally:
        engine.dispose()


def _drop_database(admin_url: str, name: str) -> None:
    """Drop database ``name`` if it exists, terminating stragglers first.

    ``name`` is passed explicitly rather than derived from ``admin_url``: the
    admin DSN points at the maintenance ``postgres`` database, so deriving from
    it would produce ``DROP DATABASE postgres`` -- which PostgreSQL refuses,
    because that is the very database issuing the drop.
    """
    _admin_statements(
        admin_url,
        [
            (
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :name AND pid <> pg_backend_pid()"
            ),
            f'DROP DATABASE IF EXISTS "{name}"',
        ],
        name=name,
    )


def _create_database(admin_url: str, name: str) -> None:
    """Create an empty database ``name``.

    The identifier is interpolated rather than bound: PostgreSQL's ``CREATE
    DATABASE`` takes a literal name, not a parameter. ``name`` is a module
    constant, never caller input.
    """
    _admin_statements(admin_url, [f'CREATE DATABASE "{name}"'])


def _upgrade_test_database(test_url: str) -> None:
    """Bring the test database to ``head`` using the real Alembic environment.

    Running the actual migration rather than ``Base.metadata.create_all`` is the
    point: it proves the committed migration produces a schema the models and
    repositories can actually use.
    """
    config = Config(API_ROOT / "alembic.ini")
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    # env.py prefers an explicit sqlalchemy.url override over application
    # settings, so this redirects the run at the test database.
    config.set_main_option("sqlalchemy.url", test_url)
    command.upgrade(config, "head")


@pytest.fixture(scope="session")
def postgres_admin_url() -> str:
    """Return the administrative DSN, skipping the layer if PostgreSQL is absent."""
    url = _admin_url()
    engine = sa.create_engine(url, connect_args={"connect_timeout": 3}, poolclass=sa.pool.NullPool)
    try:
        with engine.connect() as connection:
            connection.execute(sa.text("SELECT 1"))
    except sa.exc.SQLAlchemyError as exc:
        pytest.skip(f"PostgreSQL is not reachable for integration tests: {type(exc).__name__}")
    finally:
        engine.dispose()
    return url


@pytest.fixture(scope="session")
def integration_engine(postgres_admin_url: str) -> Iterator[Engine]:
    """Create the test database, migrate it, and yield a connection engine.

    Session-scoped, so the migration runs once for the whole layer rather than
    per test.
    """
    # Start from a clean slate so a previous interrupted run cannot leave a
    # half-migrated database behind and produce a misleading failure.
    _drop_database(postgres_admin_url, TEST_DB_NAME)
    _create_database(postgres_admin_url, TEST_DB_NAME)

    test_url = _test_url(postgres_admin_url)
    _upgrade_test_database(test_url)

    engine = sa.create_engine(test_url, poolclass=sa.pool.NullPool)
    try:
        yield engine
    finally:
        engine.dispose()
        _drop_database(postgres_admin_url, TEST_DB_NAME)


@pytest.fixture
def db_session(integration_engine: Engine) -> Iterator[Session]:
    """Yield a session whose writes are rolled back at the end of the test.

    The session joins a SAVEPOINT on the connection's outer transaction, so a
    test may call ``commit()`` freely -- every statement is still discarded when
    the outer transaction rolls back, and the next test starts from an empty
    schema with no truncation pass.
    """
    connection = integration_engine.connect()
    outer = connection.begin()
    session = Session(
        bind=connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
        # Autoflush is on here, unlike the application's session factory. These
        # tests assert on schema behaviour rather than on flush timing, and
        # "add a row, then query for it" should behave the way a reader expects.
        # Nothing about the schema or the constraints under test depends on it.
        autoflush=True,
    )
    try:
        yield session
    finally:
        session.close()
        if outer.is_active:
            outer.rollback()
        connection.close()


def assert_exists(session: Session, model: type[Any], **filters: object) -> bool:
    """Return whether exactly-matching rows exist for ``model``.

    Small helper so assertions read as intent rather than as query plumbing.
    """
    statement: sa.Select[tuple[Any]] = sa.select(model).filter_by(**filters)
    return session.scalar(statement) is not None
