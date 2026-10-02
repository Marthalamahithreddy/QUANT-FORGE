"""Establish the Alembic -> SQLAlchemy metadata -> PostgreSQL chain.

This revision is intentionally empty, and that is the point.

QuantForge has no application tables at this milestone: ``Base.metadata`` is
empty because no price, fundamental, filing or earnings schema has been
designed against real source data yet. Inventing tables here would lock a
guess into migration history, where it is expensive to undo.

What this revision proves is the plumbing itself:

* ``alembic/env.py`` resolves the target from ``QF_DATABASE_URL`` via
  :class:`~quantforge.core.config.Settings`, with no credential in
  ``alembic.ini``;
* ``alembic/env.py`` imports ``quantforge.db.base.Base`` and hands its
  ``metadata`` to Alembic as the autogenerate target;
* ``alembic upgrade head`` connects through SQLAlchemy's ``postgresql+psycopg``
  dialect and applies the revision, which creates the ``alembic_version`` table.

So after applying this, the database is at head, ``alembic current`` reports
``0001``, and a future ``alembic revision --autogenerate`` will diff real models
against real tables. No placeholder schema is introduced.

Revision ID: 0001
Revises:
Create Date: 2026-09-27 11:16:14.082711

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the baseline.

    No operations: there is no schema to create yet. Applying this revision is
    what establishes the version-tracking table.
    """


def downgrade() -> None:
    """Revert the baseline.

    Alembic drops the ``alembic_version`` bookkeeping itself, so there is nothing
    to undo here.
    """
