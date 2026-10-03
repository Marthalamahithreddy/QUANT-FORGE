"""Explicit availability basis for earnings observations.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-03

Adds ``earnings_event.observed_at_basis`` (``VARCHAR(32)``, nullable, ``CHECK`` in
``SOURCE_PUBLICATION``/``SEC_FILING_ACCEPTANCE``) and the pairing constraint
``(observed_at IS NULL) = (observed_at_basis IS NULL)``.

Why
---
A bare ``observed_at`` cannot say what it proves: a provider's own publication
instant, the acceptance time of an SEC filing and a record-modified time are
different facts. The basis names the evidence; which bases count as
point-in-time availability is policy in application code
(``APPROVED_AVAILABILITY_BASES``), not here.

Existing rows
-------------
Every row written so far has ``observed_at IS NULL`` (the Finnhub mapper maps it
to ``None`` and nothing else has ever written an earnings row), so the new column
is NULL for all of them and the pairing constraint holds. The upgrade *checks*
this instead of assuming it: if any row already has an ``observed_at``, no basis
can be assigned honestly, so the migration refuses and says so.

Downgrade
---------
Dropping the column loses what each timestamp proved, so it refuses while any
row carries a basis.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BASES = ("SOURCE_PUBLICATION", "SEC_FILING_ACCEPTANCE")


def upgrade() -> None:
    """Upgrade schema."""
    if op.get_context().as_sql:
        message = "this migration verifies existing rows and needs a live database (not --sql)"
        raise RuntimeError(message)
    connection = op.get_bind()
    stamped = connection.execute(
        sa.text("SELECT count(*) FROM earnings_event WHERE observed_at IS NOT NULL")
    ).scalar_one()
    if stamped:
        message = (
            f"{stamped} earnings_event row(s) already have an observed_at but no stated basis; "
            "assigning one would be a guess. Resolve those rows deliberately (state what each "
            "timestamp proves, or clear it) before applying 0007."
        )
        raise RuntimeError(message)

    op.add_column(
        "earnings_event", sa.Column("observed_at_basis", sa.String(length=32), nullable=True)
    )
    literals = ", ".join(f"'{basis}'" for basis in _BASES)
    op.create_check_constraint(
        op.f("ck_earnings_event_observed_at_basis_enum_values"),
        "earnings_event",
        f"observed_at_basis IN ({literals})",
    )
    op.create_check_constraint(
        op.f("ck_earnings_event_observed_at_basis_pairing"),
        "earnings_event",
        "(observed_at IS NULL) = (observed_at_basis IS NULL)",
    )


def downgrade() -> None:
    """Downgrade schema.

    Refuses while any row carries a basis: dropping the column would discard what
    its ``observed_at`` was evidence of.
    """
    if op.get_context().as_sql:
        message = "this downgrade verifies existing rows and needs a live database (not --sql)"
        raise RuntimeError(message)
    connection = op.get_bind()
    carried = connection.execute(
        sa.text("SELECT count(*) FROM earnings_event WHERE observed_at_basis IS NOT NULL")
    ).scalar_one()
    if carried:
        message = (
            f"{carried} earnings_event row(s) carry an observed_at_basis; downgrading would "
            "discard what their timestamps prove. Clear or export them deliberately first."
        )
        raise RuntimeError(message)
    op.drop_constraint(
        op.f("ck_earnings_event_observed_at_basis_pairing"), "earnings_event", type_="check"
    )
    op.drop_constraint(
        op.f("ck_earnings_event_observed_at_basis_enum_values"), "earnings_event", type_="check"
    )
    op.drop_column("earnings_event", "observed_at_basis")
