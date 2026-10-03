"""Sourced historical listing facts; FIGI share-class identifier level.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-03

Adds ``listing_history_fact``: single-date statements a provider makes about a security's
ticker (observation, ticker start, last traded), each with its source and run. Intervals are
derived from facts, never stored, and ``Listing.valid_from`` keeps meaning "first observed".

Also adds ``IdentifierType.FIGI_SHARE_CLASS`` to the identifier CHECK. An OpenFIGI code exists at
several levels, and the legacy ``FIGI`` type does not say which. Rows are relabelled
``FIGI_SHARE_CLASS`` ONLY where provenance proves it: ``source_id`` is the Massive source, and the
Massive reference adapter has only ever stored ``share_class_figi`` under ``FIGI``. A row with no
source, or any other source, is left as legacy ``FIGI`` -- never guessed. The new fact table is
empty and no historical provenance is backfilled; first-observed ``Listing`` dates are not
converted into source-stated ones.

A partial unique index makes one FIGI literal name one security across ``FIGI`` and
``FIGI_SHARE_CLASS``. The upgrade fails loudly (the index cannot build) if existing data already
has the same literal under two securities -- that ambiguity is never merged or guessed.

Downgrade restores the old vocabulary (``FIGI_SHARE_CLASS`` rows revert to ``FIGI``, a lossless
direction), and refuses while any ``listing_history_fact`` row exists so that discarding sourced
history is always explicit.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_OLD = "identifier_type IN ('ISIN', 'CUSIP', 'SEDOL', 'FIGI', 'OTHER')"
_NEW = "identifier_type IN ('ISIN', 'CUSIP', 'SEDOL', 'FIGI', 'FIGI_SHARE_CLASS', 'OTHER')"
_CK = "ck_security_identifier_identifier_type_enum_values"


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint(op.f(_CK), "security_identifier", type_="check")
    op.create_check_constraint(op.f(_CK), "security_identifier", _NEW)
    op.execute(
        "UPDATE security_identifier SET identifier_type = 'FIGI_SHARE_CLASS' "
        "WHERE identifier_type = 'FIGI' AND source_id IN "
        "(SELECT id FROM data_source WHERE name = 'massive')"
    )
    op.create_index(
        "uq_security_identifier_figi_family_value",
        "security_identifier",
        ["identifier_value"],
        unique=True,
        postgresql_where=sa.text("identifier_type IN ('FIGI', 'FIGI_SHARE_CLASS')"),
    )
    op.create_table(
        "listing_history_fact",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("security_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "TICKER_OBSERVED",
                "TICKER_STARTED",
                "LAST_TRADED",
                name="listing_fact_kind",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("ticker", sa.String(length=32), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("mic", sa.String(length=4), nullable=True),
        sa.Column("ingestion_run_id", sa.Uuid(), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "effective_date BETWEEN DATE '1900-01-01' AND DATE '2100-12-31'",
            name=op.f("ck_listing_history_fact_effective_date_sane_range"),
        ),
        sa.CheckConstraint(
            "kind IN ('TICKER_OBSERVED', 'TICKER_STARTED', 'LAST_TRADED')",
            name=op.f("ck_listing_history_fact_kind_enum_values"),
        ),
        sa.CheckConstraint(
            "mic IS NULL OR kind = 'TICKER_OBSERVED'",
            name=op.f("ck_listing_history_fact_mic_only_on_observation"),
        ),
        sa.CheckConstraint(
            "ticker = upper(ticker) AND ticker <> ''",
            name=op.f("ck_listing_history_fact_ticker_canonical"),
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_run_id"],
            ["ingestion_run.id"],
            name=op.f("fk_listing_history_fact_ingestion_run_id_ingestion_run"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["security_id"],
            ["security.id"],
            name=op.f("fk_listing_history_fact_security_id_security"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["data_source.id"],
            name=op.f("fk_listing_history_fact_source_id_data_source"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_listing_history_fact")),
        sa.UniqueConstraint(
            "source_id",
            "security_id",
            "kind",
            "ticker",
            "effective_date",
            name="uq_listing_history_fact_identity",
        ),
    )
    op.create_index(
        "ix_listing_history_fact_ingestion_run_id",
        "listing_history_fact",
        ["ingestion_run_id"],
        unique=False,
    )
    op.create_index(
        "ix_listing_history_fact_security_id", "listing_history_fact", ["security_id"], unique=False
    )
    op.create_index(
        "ix_listing_history_fact_source_id", "listing_history_fact", ["source_id"], unique=False
    )
    op.create_index(
        "ix_listing_history_fact_ticker", "listing_history_fact", ["ticker"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema, refusing while sourced listing history exists."""
    if op.get_context().as_sql:
        message = "this downgrade verifies existing rows and needs a live database (not --sql)"
        raise RuntimeError(message)
    existing = op.get_bind().execute(sa.text("SELECT count(*) FROM listing_history_fact")).scalar_one()
    if existing:
        message = (
            f"{existing} listing_history_fact row(s) exist; downgrading past 0010 would discard "
            "sourced listing history. Delete or export them deliberately first."
        )
        raise RuntimeError(message)
    op.drop_index("ix_listing_history_fact_ticker", table_name="listing_history_fact")
    op.drop_index("ix_listing_history_fact_source_id", table_name="listing_history_fact")
    op.drop_index("ix_listing_history_fact_security_id", table_name="listing_history_fact")
    op.drop_index("ix_listing_history_fact_ingestion_run_id", table_name="listing_history_fact")
    op.drop_table("listing_history_fact")
    op.drop_index("uq_security_identifier_figi_family_value", table_name="security_identifier")
    op.execute(
        "UPDATE security_identifier SET identifier_type = 'FIGI' "
        "WHERE identifier_type = 'FIGI_SHARE_CLASS'"
    )
    op.drop_constraint(op.f(_CK), "security_identifier", type_="check")
    op.create_check_constraint(op.f(_CK), "security_identifier", _OLD)
