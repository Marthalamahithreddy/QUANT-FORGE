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

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from quantforge.core.config import Settings, get_settings
from quantforge.core.dependencies import DependencyCheck, DependencyName

logger = logging.getLogger(__name__)

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
        self._session_factory: sessionmaker[Session] | None = None

    def engine(self, database_url: str) -> Engine:
        """Return the engine for ``database_url``, creating it if required."""
        if self._engine is None or self._database_url != database_url:
            self.dispose()
            self._engine = create_engine(database_url, pool_pre_ping=True)
            self._database_url = database_url
        return self._engine

    def session_factory(self, database_url: str) -> sessionmaker[Session]:
        """Return the session factory for ``database_url``, creating it if required."""
        if self._session_factory is None:
            self._session_factory = sessionmaker(
                bind=self.engine(database_url),
                autoflush=False,
                expire_on_commit=False,
            )
        return self._session_factory

    def dispose(self) -> None:
        """Dispose the engine and drop cached state."""
        if self._engine is not None:
            self._engine.dispose()
        self._engine = None
        self._database_url = None
        self._session_factory = None


_registry = _EngineRegistry()


def _resolve_database_url(settings: Settings | None) -> str:
    resolved = settings if settings is not None else get_settings()
    return normalise_database_url(resolved.database_url)


def get_engine(settings: Settings | None = None) -> Engine:
    """Return the process-wide :class:`~sqlalchemy.Engine`."""
    return _registry.engine(_resolve_database_url(settings))


def get_session_factory(settings: Settings | None = None) -> sessionmaker[Session]:
    """Return the process-wide :class:`~sqlalchemy.orm.sessionmaker`."""
    return _registry.session_factory(_resolve_database_url(settings))


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


def ping_database(settings: Settings | None = None) -> DependencyCheck:
    """Probe PostgreSQL by executing a trivial statement.

    ``pool_pre_ping`` already discards connections that died while idle, so a
    single ``SELECT 1`` is enough to prove the server is reachable and accepting
    queries.

    Returns:
        A :class:`~quantforge.core.dependencies.DependencyCheck` that never
        raises. On failure the detail is the exception type name only, because
        SQLAlchemy exception text embeds the DSN.
    """
    try:
        with get_engine(settings).connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:  # any driver error means the dependency is unavailable
        logger.warning("PostgreSQL readiness probe failed.", exc_info=True)
        return DependencyCheck.failure(DependencyName.DATABASE, exc)

    return DependencyCheck.ok(DependencyName.DATABASE)
