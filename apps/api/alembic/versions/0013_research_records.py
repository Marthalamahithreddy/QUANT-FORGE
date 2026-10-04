"""Benchmark bar run provenance and immutable research records.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-04

Two changes, both justified by Step 10.9B:

* ``benchmark_price_bar.ingestion_run_id`` (nullable FK to ``ingestion_run``, indexed), so a NEW
  benchmark level records the run that wrote it, like ``daily_price_bar``. Nullable on purpose and
  NOT backfilled: existing rows have no exact authoritative run provenance, and none is inferred
  from timestamps, source, symbol, date or the nearest run. They stay NULL (honestly "unknown").
* ``research_observation_record``: one immutable baseline per completed research computation
  (canonical manifest + canonical result as TEXT, their SHA-256 digests, derived level and gaps).
  Triggers reject any UPDATE, DELETE or TRUNCATE, so a baseline can only be added, never altered
  (a superuser can still disable triggers deliberately).

Downgrade refuses while research records exist, or while any benchmark level carries a run id,
so discarding historical baselines or provenance is always an explicit act.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_GUARD_FUNCTION = "research_observation_record_immutable"
_GUARD_TRIGGER = "trg_research_observation_record_immutable"
_GUARD_TRUNCATE_TRIGGER = "trg_research_observation_record_no_truncate"


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "benchmark_price_bar", sa.Column("ingestion_run_id", sa.Uuid(), nullable=True)
    )
    op.create_foreign_key(
        op.f("fk_benchmark_price_bar_ingestion_run_id_ingestion_run"),
        "benchmark_price_bar",
        "ingestion_run",
        ["ingestion_run_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_benchmark_price_bar_ingestion_run_id",
        "benchmark_price_bar",
        ["ingestion_run_id"],
        unique=False,
    )
    op.create_table(
        "research_observation_record",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("manifest_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("manifest_hash_algorithm", sa.String(length=16), nullable=False),
        sa.Column("canonical_manifest", sa.Text(), nullable=False),
        sa.Column("result_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("result_hash_algorithm", sa.String(length=16), nullable=False),
        sa.Column("canonical_result", sa.Text(), nullable=False),
        sa.Column(
            "reproducibility_level",
            sa.Enum(
                "COMPLETE",
                "PARTIAL",
                name="reproducibility_level",
                native_enum=False,
                create_constraint=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("reproducibility_gaps", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint(
            "manifest_fingerprint ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_research_observation_record_manifest_fingerprint_format"),
        ),
        sa.CheckConstraint(
            "result_fingerprint ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_research_observation_record_result_fingerprint_format"),
        ),
        sa.CheckConstraint(
            "manifest_hash_algorithm = 'sha256'",
            name=op.f("ck_research_observation_record_manifest_hash_algorithm_known"),
        ),
        sa.CheckConstraint(
            "result_hash_algorithm = 'sha256'",
            name=op.f("ck_research_observation_record_result_hash_algorithm_known"),
        ),
        sa.CheckConstraint(
            "length(canonical_manifest) > 0",
            name=op.f("ck_research_observation_record_canonical_manifest_nonempty"),
        ),
        sa.CheckConstraint(
            "length(canonical_result) > 0",
            name=op.f("ck_research_observation_record_canonical_result_nonempty"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(reproducibility_gaps) = 'array'",
            name=op.f("ck_research_observation_record_gaps_is_array"),
        ),
        sa.CheckConstraint(
            "(reproducibility_level = 'COMPLETE') = (jsonb_array_length(reproducibility_gaps) = 0)",
            name=op.f("ck_research_observation_record_level_matches_gaps"),
        ),
        sa.CheckConstraint(
            "reproducibility_level IN ('COMPLETE', 'PARTIAL')",
            name=op.f("ck_research_observation_record_reproducibility_level_enum_values"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_research_observation_record")),
        sa.UniqueConstraint(
            "manifest_fingerprint",
            "result_fingerprint",
            name="uq_research_observation_record_manifest_result",
        ),
    )
    op.execute(
        f"""
        CREATE FUNCTION {_GUARD_FUNCTION}() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'research_observation_record is immutable: % is not allowed', TG_OP
                USING ERRCODE = 'integrity_constraint_violation';
        END;
        $$
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {_GUARD_TRIGGER}
        BEFORE UPDATE OR DELETE ON research_observation_record
        FOR EACH ROW EXECUTE FUNCTION {_GUARD_FUNCTION}()
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {_GUARD_TRUNCATE_TRIGGER}
        BEFORE TRUNCATE ON research_observation_record
        FOR EACH STATEMENT EXECUTE FUNCTION {_GUARD_FUNCTION}()
        """
    )


def downgrade() -> None:
    """Downgrade schema, refusing while baselines or benchmark run provenance exist."""
    if op.get_context().as_sql:
        message = "this downgrade verifies existing rows and needs a live database (not --sql)"
        raise RuntimeError(message)
    bind = op.get_bind()
    records = bind.execute(sa.text("SELECT count(*) FROM research_observation_record")).scalar_one()
    stamped = bind.execute(
        sa.text("SELECT count(*) FROM benchmark_price_bar WHERE ingestion_run_id IS NOT NULL")
    ).scalar_one()
    if records or stamped:
        message = (
            f"{records} research_observation_record row(s) and {stamped} benchmark level(s) with "
            "run provenance exist; downgrading past 0013 would discard historical baselines or "
            "provenance. Remove them deliberately first."
        )
        raise RuntimeError(message)
    op.execute(f"DROP TRIGGER {_GUARD_TRUNCATE_TRIGGER} ON research_observation_record")
    op.execute(f"DROP TRIGGER {_GUARD_TRIGGER} ON research_observation_record")
    op.execute(f"DROP FUNCTION {_GUARD_FUNCTION}()")
    op.drop_table("research_observation_record")
    op.drop_index("ix_benchmark_price_bar_ingestion_run_id", table_name="benchmark_price_bar")
    op.drop_constraint(
        op.f("fk_benchmark_price_bar_ingestion_run_id_ingestion_run"),
        "benchmark_price_bar",
        type_="foreignkey",
    )
    op.drop_column("benchmark_price_bar", "ingestion_run_id")
