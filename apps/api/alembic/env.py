"""Alembic environment for QuantForge.

Three deliberate choices:

* the database URL comes from :class:`~quantforge.core.config.Settings`, not from
  ``alembic.ini``, so migrations always target the same database as the API and
  no credential is ever written to a committed file;
* ``Base.metadata`` is the single autogenerate target, so a model added anywhere
  under ``quantforge`` is detected without editing this file;
* :mod:`quantforge.models` is imported explicitly below. Metadata is populated
  by module *import*, not by some global registry, so without that import
  autogenerate would see an empty target and silently emit a no-op migration --
  the most dangerous possible failure mode for a migration tool.
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import Connection, engine_from_config, pool

# Make the package importable when Alembic runs from a bare checkout.
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import quantforge.models  # noqa: E402, F401  (import registers every table)
from quantforge.core.config import get_settings  # noqa: E402
from quantforge.db.base import Base  # noqa: E402
from quantforge.db.session import normalise_database_url  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Autogenerate target, fully populated by the import above.
target_metadata = Base.metadata


def get_url() -> str:
    """Return the migration target URL from application settings.

    Precedence is the standard Alembic one: ``-x url=...`` or
    ``sqlalchemy.url`` in the config override, otherwise ``QF_DATABASE_URL``.
    """
    override = config.get_main_option("sqlalchemy.url", "")
    if override:
        return normalise_database_url(override)
    return normalise_database_url(get_settings().database_url)


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a live database connection."""
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def _configure(connection: Connection) -> None:
    """Apply the shared autogenerate options to a migration context."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )


def run_migrations_online() -> None:
    """Run migrations against a live connection.

    The URL is injected here rather than read from the ini file so credentials
    stay out of version control.
    """
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = get_url()

    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    try:
        with connectable.connect() as connection:
            _configure(connection)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        # Disposed unconditionally: a failed migration must not leak the
        # connection, or the test suite trips over the ResourceWarning.
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
