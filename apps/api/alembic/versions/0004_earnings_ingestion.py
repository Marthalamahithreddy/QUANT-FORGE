"""Earnings exact-observation identity.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-28

Adds ``earnings_event.fingerprint`` and a unique index over
``(source_id, company_id, fingerprint)``.

Why the column and not a constraint on the natural columns
----------------------------------------------------------
The duplicate policy is "an exact re-fetch of the same fact from the same
source is a no-op; any factual change is a new, preserved revision". A unique
constraint over the four figures, quarter and currency cannot express that:
those columns are nullable, and PostgreSQL's unique semantics treat NULLs as
distinct, so two identical observations missing an optional figure would never
collide. A single NOT NULL digest column collapses exactly the observations
whose facts match, and only those.

This is the *smallest* justified change, and it does not touch the existing
period-triple design. ``(company_id, fiscal_year, fiscal_quarter)`` is still
deliberately non-unique: a period legitimately holds several observations (a
preliminary release, a restatement, a second vendor), each with its own
fingerprint. The unique index is finer and per-source, so different sources
never collapse.

The table is empty at this revision (earnings ingestion is new in this
milestone), so adding a NOT NULL column without a server default is safe.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "earnings_event",
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
    )
    op.create_unique_constraint(
        op.f("uq_earnings_event_source_company_fingerprint"),
        "earnings_event",
        ["source_id", "company_id", "fingerprint"],
    )
    # Hand-written, for the same reason `daily_price_bar.ingestion_run_id` was
    # in 0003: the link from a written row back to the run that wrote it, added
    # once `ingestion_run` exists (it is not created in this migration). Nullable
    # so an event created outside a run is representable.
    op.add_column(
        "earnings_event",
        sa.Column(
            "ingestion_run_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("ingestion_run.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_earnings_event_ingestion_run_id",
        "earnings_event",
        ["ingestion_run_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_earnings_event_ingestion_run_id", table_name="earnings_event")
    op.drop_constraint(
        op.f("fk_earnings_event_ingestion_run_id_ingestion_run"),
        "earnings_event",
        type_="foreignkey",
    )
    op.drop_column("earnings_event", "ingestion_run_id")
    op.drop_constraint(
        op.f("uq_earnings_event_source_company_fingerprint"),
        "earnings_event",
        type_="unique",
    )
    op.drop_column("earnings_event", "fingerprint")
