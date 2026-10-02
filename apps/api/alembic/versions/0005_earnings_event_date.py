"""Earnings temporal precision: separate calendar date from exact instant.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-01

Adds ``earnings_event.event_date`` (``DATE``, ``NOT NULL``) and makes
``earnings_event.event_timestamp`` nullable.

Why
---
Every earnings-calendar source integrated so far states a calendar date; not
every source states an exact instant. The prior schema required
``event_timestamp`` unconditionally, which left exactly two honest choices for
a date-only source: fabricate a fake instant (midnight, 09:30, 16:00 -- all
equally invented), or refuse to store the observation at all. Neither is
correct. This migration makes the schema say what is actually true: the date
is always required, the instant is required only when a source genuinely
supplies one.

Backfill
--------
Every row already in the table (this milestone's ``0004`` never shipped a
working adapter, so in practice there are none in any environment that has
run migrations in order) has a non-null ``event_timestamp`` that predates
this migration -- by definition, since ``event_date`` did not exist yet. Its
``event_timestamp`` was stored as "exactly as the source stated it", an aware
instant; ``event_date`` is backfilled as that instant's own UTC calendar date,
which is the only value derivable from the row without inventing a new fact.

The extraction goes through ``AT TIME ZONE 'UTC'`` explicitly rather than a
bare ``::date`` cast, because a bare cast on a ``timestamptz`` column
interprets it in the *session's* ``timezone`` GUC, which is not guaranteed to
be UTC and would silently shift the derived date by a day depending on who
runs the migration and how their client is configured. Going through UTC
first pins the answer to the same UTC-canonical convention already used
everywhere else in this schema (``ingested_at``, ``observed_at``, and the
`founded_date`/`_today()` policy in the schemas layer).

Downgrade
---------
Reversing this migration requires every row to have a non-null
``event_timestamp`` again, which a date-only row (anything ingested through
the Finnhub-shaped path this migration exists to support) does not have.
Downgrading in that state would require fabricating an instant -- the exact
thing this migration exists to stop doing -- so the downgrade path refuses
loudly instead, and tells the operator what to do about it, rather than
silently inventing data on the way out.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Nullable at first so the backfill below can populate every existing row
    # before the NOT NULL constraint is applied.
    op.add_column(
        "earnings_event",
        sa.Column("event_date", sa.Date(), nullable=True),
    )
    op.execute(
        "UPDATE earnings_event "
        "SET event_date = (event_timestamp AT TIME ZONE 'UTC')::date "
        "WHERE event_date IS NULL"
    )
    op.alter_column("earnings_event", "event_date", nullable=False)
    op.alter_column("earnings_event", "event_timestamp", nullable=True)

    op.drop_index("ix_earnings_event_company_event_ts", table_name="earnings_event")
    op.create_index(
        "ix_earnings_event_company_event_date",
        "earnings_event",
        ["company_id", "event_date"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema.

    Refuses if any row has a NULL ``event_timestamp``: restoring the old
    NOT NULL constraint on that column would otherwise require fabricating an
    instant for a date-only observation, which this migration exists to
    prevent even on the way out.
    """
    connection = op.get_bind()
    orphaned = connection.execute(
        sa.text("SELECT count(*) FROM earnings_event WHERE event_timestamp IS NULL")
    ).scalar_one()
    if orphaned:
        message = (
            f"{orphaned} earnings_event row(s) have no event_timestamp and cannot "
            "be downgraded without fabricating one. Resolve or remove those rows "
            "before downgrading past 0005 -- for example, by deleting the "
            "date-only observations if they are safe to lose, or by keeping this "
            "migration applied."
        )
        raise RuntimeError(message)

    op.drop_index("ix_earnings_event_company_event_date", table_name="earnings_event")
    op.create_index(
        "ix_earnings_event_company_event_ts",
        "earnings_event",
        ["company_id", "event_timestamp"],
        unique=False,
    )

    op.alter_column("earnings_event", "event_timestamp", nullable=False)
    op.drop_column("earnings_event", "event_date")
