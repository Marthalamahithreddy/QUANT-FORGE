"""SEC filing metadata table.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-03

Adds ``sec_filing``: one row per ``(company, accession)`` carrying only what the
SEC states about a submission (accession, form, filing/report dates, acceptance
instant, primary document, 8-K item codes) plus provenance. Filings are not
earnings events and are not stored in ``earnings_event``; the table is new, so
there is nothing to backfill. ``accepted_at`` proves EDGAR acceptance only, never
first public disclosure -- see ``quantforge.models.filing``.

Downgrade drops the table (it holds only re-fetchable source metadata), refusing
while any rows exist so that discarding ingested history is always explicit.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "sec_filing",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("accession_number", sa.String(length=20), nullable=False),
        sa.Column(
            "form",
            sa.Enum(
                "10-K",
                "10-K/A",
                "10-Q",
                "10-Q/A",
                "8-K",
                "8-K/A",
                name="sec_form_type",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("filing_date", sa.Date(), nullable=False),
        sa.Column("report_date", sa.Date(), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("primary_document", sa.String(length=255), nullable=True),
        sa.Column("items", sa.String(length=256), nullable=True),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("ingestion_run_id", sa.Uuid(), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "accession_number ~ '^[0-9]{10}-[0-9]{2}-[0-9]{6}$'",
            name=op.f("ck_sec_filing_accession_number_format"),
        ),
        sa.CheckConstraint(
            "filing_date >= DATE '1993-01-01'", name=op.f("ck_sec_filing_filing_date_edgar_era")
        ),
        sa.CheckConstraint(
            "form IN ('10-K', '10-K/A', '10-Q', '10-Q/A', '8-K', '8-K/A')",
            name=op.f("ck_sec_filing_form_enum_values"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["company.id"],
            name=op.f("fk_sec_filing_company_id_company"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_run_id"],
            ["ingestion_run.id"],
            name=op.f("fk_sec_filing_ingestion_run_id_ingestion_run"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["data_source.id"],
            name=op.f("fk_sec_filing_source_id_data_source"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sec_filing")),
        sa.UniqueConstraint(
            "company_id", "accession_number", name="uq_sec_filing_company_accession"
        ),
    )
    op.create_index(
        "ix_sec_filing_company_id_filing_date",
        "sec_filing",
        ["company_id", "filing_date"],
        unique=False,
    )
    op.create_index(
        "ix_sec_filing_ingestion_run_id", "sec_filing", ["ingestion_run_id"], unique=False
    )
    op.create_index("ix_sec_filing_source_id", "sec_filing", ["source_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema, refusing while ingested filings exist."""
    if op.get_context().as_sql:
        message = "this downgrade verifies existing rows and needs a live database (not --sql)"
        raise RuntimeError(message)
    existing = op.get_bind().execute(sa.text("SELECT count(*) FROM sec_filing")).scalar_one()
    if existing:
        message = (
            f"{existing} sec_filing row(s) exist; downgrading past 0008 would discard ingested "
            "filing history. Delete or export them deliberately first."
        )
        raise RuntimeError(message)
    op.drop_index("ix_sec_filing_source_id", table_name="sec_filing")
    op.drop_index("ix_sec_filing_ingestion_run_id", table_name="sec_filing")
    op.drop_index("ix_sec_filing_company_id_filing_date", table_name="sec_filing")
    op.drop_table("sec_filing")
