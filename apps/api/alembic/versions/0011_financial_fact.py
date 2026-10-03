"""Accession-bound financial facts and the structured-source artifacts behind them.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-03

Adds ``financial_fact_artifact`` (one row per distinct package content, e.g. one SEC
Financial Statement Data Sets quarterly ZIP identified by SHA-256),
``financial_fact_submission`` (what an artifact stated about one stored filing) and
``financial_fact`` (one source-stated numeric fact bound to one ``sec_filing``,
unique on the FSDS NUM key with the accession resolved to a filing; a composite foreign
key keeps a fact's submission on the same filing as the fact). All three tables are new,
so there is nothing to backfill. Nothing here carries an availability timestamp:
``sec_filing.accepted_at`` is not when a fact in a bulk artifact became available.

Downgrade drops all three (they hold only re-extractable source facts and provenance),
refusing while any has rows so that discarding extracted facts is always explicit.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "financial_fact_artifact",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column(
            "source_kind",
            sa.Enum("SEC_FSDS", name="financial_fact_source_kind", native_enum=False, length=16),
            nullable=False,
        ),
        sa.Column("artifact_name", sa.String(length=64), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("byte_length", sa.BigInteger(), nullable=False),
        sa.Column("raw_artifact_path", sa.String(length=512), nullable=False),
        sa.Column("member_digests", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("num_has_segments", sa.Boolean(), nullable=False),
        sa.Column("ingestion_run_id", sa.Uuid(), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_financial_fact_artifact_sha256_format")
        ),
        sa.CheckConstraint(
            "source_kind IN ('SEC_FSDS')",
            name=op.f("ck_financial_fact_artifact_source_kind_enum_values"),
        ),
        sa.CheckConstraint(
            "byte_length > 0", name=op.f("ck_financial_fact_artifact_byte_length_positive")
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_run_id"],
            ["ingestion_run.id"],
            name=op.f("fk_financial_fact_artifact_ingestion_run_id_ingestion_run"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["data_source.id"],
            name=op.f("fk_financial_fact_artifact_source_id_data_source"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_financial_fact_artifact")),
        sa.UniqueConstraint(
            "source_kind", "artifact_name", "sha256", name="uq_financial_fact_artifact_content"
        ),
    )
    op.create_index(
        "ix_financial_fact_artifact_ingestion_run_id",
        "financial_fact_artifact",
        ["ingestion_run_id"],
        unique=False,
    )
    op.create_index(
        "ix_financial_fact_artifact_source_id",
        "financial_fact_artifact",
        ["source_id"],
        unique=False,
    )
    op.create_table(
        "financial_fact_submission",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("artifact_id", sa.Uuid(), nullable=False),
        sa.Column("sec_filing_id", sa.Uuid(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("fiscal_year", sa.SmallInteger(), nullable=True),
        sa.Column("fiscal_period", sa.String(length=2), nullable=True),
        sa.Column("fiscal_year_end", sa.String(length=4), nullable=True),
        sa.Column("source_accepted", sa.String(length=19), nullable=True),
        sa.Column("coregistrant_count", sa.SmallInteger(), nullable=False),
        sa.Column("additional_ciks", sa.String(length=120), nullable=True),
        sa.CheckConstraint(
            "period_end >= DATE '1900-01-01' AND period_end <= DATE '2100-12-31'",
            name=op.f("ck_financial_fact_submission_period_end_range"),
        ),
        sa.CheckConstraint(
            "coregistrant_count >= 1",
            name=op.f("ck_financial_fact_submission_coregistrant_count_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id"],
            ["financial_fact_artifact.id"],
            name=op.f("fk_financial_fact_submission_artifact_id_financial_fact_artifact"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["sec_filing_id"],
            ["sec_filing.id"],
            name=op.f("fk_financial_fact_submission_sec_filing_id_sec_filing"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_financial_fact_submission")),
        sa.UniqueConstraint(
            "artifact_id", "sec_filing_id", name="uq_financial_fact_submission_artifact_filing"
        ),
        sa.UniqueConstraint("id", "sec_filing_id", name="uq_financial_fact_submission_id_filing"),
    )
    op.create_index(
        "ix_financial_fact_submission_sec_filing_id",
        "financial_fact_submission",
        ["sec_filing_id"],
        unique=False,
    )
    op.create_table(
        "financial_fact",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sec_filing_id", sa.Uuid(), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("tag", sa.String(length=256), nullable=False),
        sa.Column("version", sa.String(length=20), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("duration_quarters", sa.SmallInteger(), nullable=False),
        sa.Column("unit", sa.String(length=20), nullable=False),
        sa.Column("segments", sa.String(length=1024), nullable=False),
        sa.Column("coreg", sa.String(length=256), nullable=False),
        sa.Column("value", sa.Numeric(precision=28, scale=4), nullable=True),
        sa.Column("ingestion_run_id", sa.Uuid(), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "period_end >= DATE '1900-01-01' AND period_end <= DATE '2100-12-31'",
            name=op.f("ck_financial_fact_period_end_range"),
        ),
        sa.CheckConstraint("tag <> ''", name=op.f("ck_financial_fact_tag_not_blank")),
        sa.CheckConstraint("unit <> ''", name=op.f("ck_financial_fact_unit_not_blank")),
        sa.CheckConstraint("version <> ''", name=op.f("ck_financial_fact_version_not_blank")),
        sa.CheckConstraint(
            "duration_quarters >= 0", name=op.f("ck_financial_fact_duration_quarters_non_negative")
        ),
        sa.CheckConstraint(
            "octet_length(segments) + octet_length(coreg) <= 2000",
            name=op.f("ck_financial_fact_identity_text_bytes"),
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_run_id"],
            ["ingestion_run.id"],
            name=op.f("fk_financial_fact_ingestion_run_id_ingestion_run"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["sec_filing_id"],
            ["sec_filing.id"],
            name=op.f("fk_financial_fact_sec_filing_id_sec_filing"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["submission_id", "sec_filing_id"],
            ["financial_fact_submission.id", "financial_fact_submission.sec_filing_id"],
            name="fk_financial_fact_submission_same_filing",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_financial_fact")),
        sa.UniqueConstraint(
            "sec_filing_id",
            "tag",
            "version",
            "period_end",
            "duration_quarters",
            "unit",
            "segments",
            "coreg",
            name="uq_financial_fact_identity",
        ),
    )
    op.create_index(
        "ix_financial_fact_ingestion_run_id", "financial_fact", ["ingestion_run_id"], unique=False
    )
    op.create_index(
        "ix_financial_fact_submission_id", "financial_fact", ["submission_id"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema, refusing while extracted facts or their provenance exist."""
    if op.get_context().as_sql:
        message = "this downgrade verifies existing rows and needs a live database (not --sql)"
        raise RuntimeError(message)
    bind = op.get_bind()
    counts = {
        table: bind.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()  # noqa: S608
        for table in ("financial_fact", "financial_fact_submission", "financial_fact_artifact")
    }
    if any(counts.values()):
        detail = ", ".join(f"{count} {table}" for table, count in counts.items() if count)
        message = (
            f"{detail} row(s) exist; downgrading past 0011 would discard extracted financial "
            "facts and their provenance. Delete or export them deliberately first."
        )
        raise RuntimeError(message)
    op.drop_index("ix_financial_fact_submission_id", table_name="financial_fact")
    op.drop_index("ix_financial_fact_ingestion_run_id", table_name="financial_fact")
    op.drop_table("financial_fact")
    op.drop_index(
        "ix_financial_fact_submission_sec_filing_id", table_name="financial_fact_submission"
    )
    op.drop_table("financial_fact_submission")
    op.drop_index("ix_financial_fact_artifact_source_id", table_name="financial_fact_artifact")
    op.drop_index(
        "ix_financial_fact_artifact_ingestion_run_id", table_name="financial_fact_artifact"
    )
    op.drop_table("financial_fact_artifact")
