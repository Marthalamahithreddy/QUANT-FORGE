"""Schema compatibility: does the database carry exactly the migration head this binary expects?

Two sides, both observed and neither modified:

* **expected** -- the single head of the repository's Alembic scripts (``apps/api/alembic``), read
  from the same directory ``alembic upgrade head`` migrates from. It is not written down anywhere
  else, so a new migration cannot leave a stale copy behind. Several heads, or an unreadable script
  directory, mean no revision can be expected and compatibility is ``unknown``.
* **current** -- the single row of ``alembic_version`` in the database, read with a plain
  ``SELECT``. This module never upgrades, downgrades, stamps or repairs anything.

The rule for the release candidate is exact equality. A database *behind* the binary lacks tables the
code reads; one *ahead* may have dropped what the code needs. Neither is assumed forward-compatible.
"""

from __future__ import annotations

import functools
import logging
import re
from pathlib import Path

from alembic.script import ScriptDirectory
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from quantforge.core.dependencies import SchemaCheck, SchemaState

__all__ = ["API_ROOT", "check_schema", "expected_head", "read_current_revision"]

logger = logging.getLogger(__name__)

#: ``apps/api``: this file is ``apps/api/src/quantforge/db/schema.py``.
API_ROOT = Path(__file__).resolve().parents[3]

#: Alembic revision ids in this repository are short ``[0-9a-z_]`` strings. Anything else read from a
#: database is not echoed back through the readiness response.
_REVISION_ID = re.compile(r"^[A-Za-z0-9_]{1,32}$")


@functools.lru_cache(maxsize=1)
def expected_head() -> str | None:
    """This binary's single migration head, or ``None`` when it cannot be established."""
    try:
        heads = ScriptDirectory(str(API_ROOT / "alembic")).get_heads()
    except Exception:
        logger.warning("Migration scripts could not be read; schema compatibility is unknown.")
        return None
    return heads[0] if len(heads) == 1 else None


def read_current_revision(connection: Connection) -> str | None:
    """The database's one Alembic revision, or ``None`` when there is not exactly one.

    Raises:
        DBAPIError: the version table is missing or the query failed (the caller decides what that
            means; a missing table is an expected, quiet condition).
    """
    rows: list[object] = list(
        connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
    )
    revision = rows[0] if len(rows) == 1 else None
    return revision if isinstance(revision, str) else None


def check_schema(connection: Connection) -> SchemaCheck:
    """Compare the database's revision with this binary's head, on an open connection."""
    expected = expected_head()
    try:
        current = read_current_revision(connection)
    except DBAPIError as exc:
        # A missing version table is the expected shape of "not migrated": quiet, class name only.
        logger.warning("Schema revision could not be read: %s", type(exc.orig or exc).__name__)
        connection.rollback()  # a failed statement poisons the transaction
        return SchemaCheck(SchemaState.UNKNOWN, expected=expected)
    safe_current = current if current is not None and _REVISION_ID.fullmatch(current) else None
    if expected is None or current is None:
        return SchemaCheck(SchemaState.UNKNOWN, expected=expected, current=safe_current)
    if current == expected:
        return SchemaCheck(SchemaState.COMPATIBLE, expected=expected, current=current)
    return SchemaCheck(SchemaState.MISMATCH, expected=expected, current=safe_current)
