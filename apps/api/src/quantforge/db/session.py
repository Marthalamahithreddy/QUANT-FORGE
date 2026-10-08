"""Lazily-constructed SQLAlchemy engine and session management.

Importing this module never opens a database connection. The engine is created
on first use, and because ``create_engine`` only prepares a connection pool,
the API can be imported, constructed and serve ``/health`` with PostgreSQL and
Redis both stopped.

Raw/source datasets are kept separate from derived analytics; no tables are
declared here yet (see :mod:`quantforge.db.base`).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from quantforge.core.config import Settings, get_settings
from quantforge.core.dependencies import (
    DatabaseProbe,
    DependencyCheck,
    DependencyName,
    SchemaCheck,
    SchemaState,
)
from quantforge.db.schema import check_schema, expected_head

logger = logging.getLogger(__name__)

#: Upper bound on the readiness probe's own statements, whatever the serving statement timeout is: a
#: probe that hangs is a probe that cannot answer.
READINESS_STATEMENT_TIMEOUT_MS = 5000

#: SQLAlchemy URL scheme prefixes that mean PostgreSQL.
_POSTGRES_SCHEMES = ("postgresql", "postgres")

#: The only PostgreSQL driver this project installs. psycopg 3 is used through
#: SQLAlchemy's ``postgresql+psycopg`` dialect; ``postgresql+psycopg2`` is not a
#: dependency, and the bare ``postgresql://`` scheme would silently fall back to
#: it and fail at connection time with a confusing ImportError.
_REQUIRED_DRIVER = "psycopg"


def normalise_database_url(database_url: str) -> str:
    """Return ``database_url`` with an explicit psycopg driver.

    A bare ``postgresql://`` or ``postgres://`` URL is rewritten to
    ``postgresql+psycopg://`` so the installed driver is always selected. A URL
    that already names a driver is left untouched, and a non-PostgreSQL scheme is
    rejected outright rather than failing later with an opaque driver error.
    """
    scheme, separator, remainder = database_url.partition("://")
    if not separator:
        # The value is NOT echoed: a malformed DSN can still carry a password.
        message = "QF_DATABASE_URL must be an absolute URL such as postgresql://user@host/db."
        raise ValueError(message)

    # '+' means an explicit dialect+driver, as in postgresql+psycopg.
    base_scheme = scheme.partition("+")[0]
    if base_scheme not in _POSTGRES_SCHEMES:
        message = f"QF_DATABASE_URL must target PostgreSQL, but received scheme {base_scheme!r}."
        raise ValueError(message)

    if "+" in scheme:
        if not scheme.endswith(f"+{_REQUIRED_DRIVER}"):
            message = (
                f"QF_DATABASE_URL must use the {_REQUIRED_DRIVER!r} driver, "
                f"but received {scheme!r}."
            )
            raise ValueError(message)
        return database_url

    return f"postgresql+{_REQUIRED_DRIVER}://{remainder}"


class _EngineRegistry:
    """Holds the process-wide engine and session factory, built on demand.

    The DSN is tracked so that a settings change transparently replaces the
    engine instead of silently reusing one bound to a stale database.
    """

    def __init__(self) -> None:
        self._engine: Engine | None = None
        self._database_url: str | None = None
        self._connect_args: dict[str, Any] | None = None
        self._pool_timeout: float | None = None
        self._session_factory: sessionmaker[Session] | None = None

    def engine(
        self,
        database_url: str,
        connect_args: dict[str, Any] | None = None,
        pool_timeout: float | None = None,
    ) -> Engine:
        """Return the engine for this URL and these limits, creating it if required."""
        wanted = connect_args or {}
        if (
            self._engine is None
            or self._database_url != database_url
            or self._connect_args != wanted
            or self._pool_timeout != pool_timeout
        ):
            self.dispose()
            extra: dict[str, Any] = {} if pool_timeout is None else {"pool_timeout": pool_timeout}
            self._engine = create_engine(
                database_url, pool_pre_ping=True, connect_args=wanted, **extra
            )
            self._database_url = database_url
            self._connect_args = wanted
            self._pool_timeout = pool_timeout
        return self._engine

    def session_factory(
        self,
        database_url: str,
        connect_args: dict[str, Any] | None = None,
        pool_timeout: float | None = None,
    ) -> sessionmaker[Session]:
        """Return the session factory bound to the CURRENT engine, rebuilding it if the engine changed."""
        engine = self.engine(database_url, connect_args, pool_timeout)
        if self._session_factory is None or self._session_factory.kw.get("bind") is not engine:
            self._session_factory = sessionmaker(
                bind=engine, autoflush=False, expire_on_commit=False
            )
        return self._session_factory

    def dispose(self) -> None:
        """Dispose the engine and drop cached state."""
        if self._engine is not None:
            self._engine.dispose()
        self._engine = None
        self._database_url = None
        self._connect_args = None
        self._pool_timeout = None
        self._session_factory = None


_registry = _EngineRegistry()


def connect_arguments(settings: Settings) -> dict[str, Any]:
    """Driver arguments that bound how long the database can make a caller wait.

    ``connect_timeout`` (libpq, seconds) bounds establishing a connection; waiting for a free pooled
    connection is bounded by the same number of seconds (``pool_timeout``), so a saturated pool fails
    a request or a readiness probe quickly instead of after SQLAlchemy's 30 s default. The statement
    timeout is sent as a server option at connect time, so every statement on every pooled connection
    is bounded by PostgreSQL itself -- no application-level thread or timer is involved. A
    ``connect_timeout`` or ``options`` already present in ``QF_DATABASE_URL`` is respected (``options``
    is merged, not replaced). Poolers that reject the ``options`` startup parameter (PgBouncer without
    ``ignore_startup_parameters=options``) need that setting or a direct connection.
    """
    query = make_url(normalise_database_url(settings.database_url)).query
    arguments: dict[str, Any] = {}
    # An operator who put connect_timeout in the URL itself has made an explicit choice: leave it.
    if "connect_timeout" not in query:
        arguments["connect_timeout"] = settings.db_connect_timeout_seconds
    options = query.get("options", "")
    options = options if isinstance(options, str) else " ".join(options)
    # Keep any server options the URL already carries (for example a search_path) and add the cap,
    # unless the URL itself already sets a statement_timeout.
    if "statement_timeout" not in options:
        cap = f"-c statement_timeout={settings.db_statement_timeout_seconds * 1000}"
        arguments["options"] = f"{options} {cap}".strip()
    return arguments


def _resolve_database_url(settings: Settings | None) -> str:
    resolved = settings if settings is not None else get_settings()
    return normalise_database_url(resolved.database_url)


def get_engine(settings: Settings | None = None) -> Engine:
    """Return the process-wide :class:`~sqlalchemy.Engine`."""
    resolved = settings if settings is not None else get_settings()
    return _registry.engine(
        _resolve_database_url(resolved),
        connect_arguments(resolved),
        float(resolved.db_connect_timeout_seconds),
    )


def get_session_factory(settings: Settings | None = None) -> sessionmaker[Session]:
    """Return the process-wide :class:`~sqlalchemy.orm.sessionmaker`."""
    resolved = settings if settings is not None else get_settings()
    return _registry.session_factory(
        _resolve_database_url(resolved),
        connect_arguments(resolved),
        float(resolved.db_connect_timeout_seconds),
    )


@contextmanager
def session_scope(settings: Settings | None = None) -> Iterator[Session]:
    """Yield a :class:`~sqlalchemy.orm.Session`, committing on clean exit."""
    session = get_session_factory(settings)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session.

    Not yet wired into any route: the foundation milestone has no endpoints that
    read or write data. The transaction is rolled back rather than committed so
    a route can never accidentally persist state without an explicit commit.
    """
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def dispose_engines() -> None:
    """Dispose cached engine state. Intended for tests and maintenance scripts."""
    _registry.dispose()


#: Failures that mean "the database cannot be used right now": expected, so logged in one line with
#: no traceback. Anything else is a defect in this code and keeps its traceback.
_EXPECTED_FAILURES = (SQLAlchemyError, OSError)


def _failure_label(exc: BaseException) -> str:
    """Exception class names only (the driver's own class when SQLAlchemy wrapped one).

    Driver messages embed the DSN, host and sometimes the password, so nothing but class names is
    ever logged or returned by a probe. The full detail is deliberately not recoverable from here.
    """
    inner = getattr(exc, "orig", None)
    return (
        f"{type(exc).__name__}({type(inner).__name__})" if inner is not None else type(exc).__name__
    )


def probe_database(settings: Settings | None = None) -> DatabaseProbe:
    """One bounded round trip: is PostgreSQL usable, and is its schema this binary's schema?

    Connectivity (``SELECT 1``) and the Alembic revision are read on the same connection inside one
    transaction whose statements are capped at :data:`READINESS_STATEMENT_TIMEOUT_MS`; connecting is
    bounded by the engine's ``connect_timeout``. Nothing is written, upgraded or stamped.

    Returns:
        A :class:`~quantforge.core.dependencies.DatabaseProbe` that never raises. When the database
        cannot be reached the schema is ``unknown``. The failure detail is the exception CLASS name
        only, because driver exception text embeds the DSN.
    """
    unknown = SchemaCheck(SchemaState.UNKNOWN, expected=expected_head())
    try:
        with get_engine(settings).connect() as connection:
            connection.execute(
                text(f"SET LOCAL statement_timeout = {READINESS_STATEMENT_TIMEOUT_MS}")
            )
            connection.execute(text("SELECT 1"))
            schema = check_schema(connection)
    except _EXPECTED_FAILURES as exc:
        logger.warning("PostgreSQL readiness probe failed: %s", _failure_label(exc))
        return DatabaseProbe(DependencyCheck.failure(DependencyName.DATABASE, exc), unknown)
    except Exception as exc:
        # Not a connectivity problem: a defect. Still reported as unavailable, but with its traceback.
        logger.exception("PostgreSQL readiness probe raised an unexpected error")
        return DatabaseProbe(DependencyCheck.failure(DependencyName.DATABASE, exc), unknown)

    return DatabaseProbe(DependencyCheck.ok(DependencyName.DATABASE), schema)


def ping_database(settings: Settings | None = None) -> DependencyCheck:
    """Connectivity only: the database half of :func:`probe_database`."""
    return probe_database(settings).database
