"""Historical stock splits and split-coverage tables.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-03

Adds ``stock_split`` (one provider-stated share-count event per security, unique
on ``(source, provider split id)``) and ``stock_split_coverage`` (the security and
inclusive date ranges for which a complete split fetch succeeded, without which an
absent split must never be read as "no split"). Both tables are new, so there is
nothing to backfill. Nothing here adjusts a price or a share count.

Downgrade drops both (they hold only re-fetchable source facts and run
bookkeeping), refusing while either has rows so that discarding ingested
corporate-action history is always explicit.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "stock_split",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("security_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("provider_split_id", sa.String(length=128), nullable=False),
        sa.Column("provider_ticker", sa.String(length=32), nullable=False),
        sa.Column("execution_date", sa.Date(), nullable=False),
        sa.Column("old_shares", sa.Numeric(), nullable=False),
        sa.Column("new_shares", sa.Numeric(), nullable=False),
        sa.Column(
            "adjustment_type",
            sa.Enum(
                "FORWARD_SPLIT",
                "REVERSE_SPLIT",
                "STOCK_DIVIDEND",
                name="stock_split_adjustment_type",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("ingestion_run_id", sa.Uuid(), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "adjustment_type IN ('FORWARD_SPLIT', 'REVERSE_SPLIT', 'STOCK_DIVIDEND')",
            name=op.f("ck_stock_split_adjustment_type_enum_values"),
        ),
        sa.CheckConstraint("new_shares > 0", name=op.f("ck_stock_split_new_shares_positive")),
        sa.CheckConstraint("old_shares > 0", name=op.f("ck_stock_split_old_shares_positive")),
        sa.ForeignKeyConstraint(
            ["ingestion_run_id"],
            ["ingestion_run.id"],
            name=op.f("fk_stock_split_ingestion_run_id_ingestion_run"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["security_id"],
            ["security.id"],
            name=op.f("fk_stock_split_security_id_security"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["data_source.id"],
            name=op.f("fk_stock_split_source_id_data_source"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stock_split")),
        sa.UniqueConstraint(
            "source_id", "provider_split_id", name="uq_stock_split_source_provider_split_id"
        ),
    )
    op.create_index(
        "ix_stock_split_ingestion_run_id", "stock_split", ["ingestion_run_id"], unique=False
    )
    op.create_index(
        "ix_stock_split_security_id_execution_date",
        "stock_split",
        ["security_id", "execution_date"],
        unique=False,
    )
    op.create_index("ix_stock_split_source_id", "stock_split", ["source_id"], unique=False)
    op.create_table(
        "stock_split_coverage",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("security_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("provider_ticker", sa.String(length=32), nullable=False),
        sa.Column("covered_from", sa.Date(), nullable=False),
        sa.Column("covered_to", sa.Date(), nullable=False),
        sa.Column("ingestion_run_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "covered_from <= covered_to", name=op.f("ck_stock_split_coverage_range_ordered")
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_run_id"],
            ["ingestion_run.id"],
            name=op.f("fk_stock_split_coverage_ingestion_run_id_ingestion_run"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["security_id"],
            ["security.id"],
            name=op.f("fk_stock_split_coverage_security_id_security"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["data_source.id"],
            name=op.f("fk_stock_split_coverage_source_id_data_source"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stock_split_coverage")),
        sa.UniqueConstraint(
            "source_id",
            "security_id",
            "covered_from",
            "covered_to",
            name="uq_stock_split_coverage_range",
        ),
    )
    op.create_index(
        "ix_stock_split_coverage_ingestion_run_id",
        "stock_split_coverage",
        ["ingestion_run_id"],
        unique=False,
    )
    op.create_index(
        "ix_stock_split_coverage_security_id", "stock_split_coverage", ["security_id"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema, refusing while split history or coverage exists."""
    if op.get_context().as_sql:
        message = "this downgrade verifies existing rows and needs a live database (not --sql)"
        raise RuntimeError(message)
    bind = op.get_bind()
    splits = bind.execute(sa.text("SELECT count(*) FROM stock_split")).scalar_one()
    coverage = bind.execute(sa.text("SELECT count(*) FROM stock_split_coverage")).scalar_one()
    if splits or coverage:
        message = (
            f"{splits} stock_split and {coverage} stock_split_coverage row(s) exist; downgrading "
            "past 0009 would discard ingested corporate-action history. Delete or export "
            "them deliberately first."
        )
        raise RuntimeError(message)
    op.drop_index("ix_stock_split_coverage_security_id", table_name="stock_split_coverage")
    op.drop_index("ix_stock_split_coverage_ingestion_run_id", table_name="stock_split_coverage")
    op.drop_table("stock_split_coverage")
    op.drop_index("ix_stock_split_source_id", table_name="stock_split")
    op.drop_index("ix_stock_split_security_id_execution_date", table_name="stock_split")
    op.drop_index("ix_stock_split_ingestion_run_id", table_name="stock_split")
    op.drop_table("stock_split")
