"""Store the FSDS ``accepted`` text with room for the real source form.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-03

``financial_fact_submission.source_accepted`` was ``VARCHAR(19)`` on the documented
``yyyy-mm-dd hh:mm:ss`` form. Real FSDS rows (2026q1) state ``yyyy-mm-dd hh:mm:ss.0``, so the
column is widened to ``VARCHAR(64)``: a bound on a short source-text field, not the sampled length.
Only the type changes; the value stays verbatim, uninterpreted text with no timezone and is still
not availability evidence. Widening cannot lose data, so upgrade needs no backfill.

Downgrade narrows the column back to 19 and therefore refuses while any stored value is longer,
rather than truncating source text.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_LENGTH = 19
_NEW_LENGTH = 64


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        "financial_fact_submission",
        "source_accepted",
        existing_type=sa.String(length=_OLD_LENGTH),
        type_=sa.String(length=_NEW_LENGTH),
        existing_nullable=True,
    )


def downgrade() -> None:
    """Downgrade schema.

    Refuses if any stored ``source_accepted`` is longer than the old column: narrowing would
    silently truncate verbatim source text.
    """
    if op.get_context().as_sql:
        message = (
            "the source_accepted length check needs a live database connection; run this "
            "downgrade online, not with --sql"
        )
        raise RuntimeError(message)
    too_long = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT count(*) FROM financial_fact_submission "
                f"WHERE length(source_accepted) > {_OLD_LENGTH}"  # noqa: S608
            )
        )
        .scalar_one()
    )
    if too_long:
        message = (
            f"{too_long} financial_fact_submission row(s) hold a source_accepted longer than "
            f"{_OLD_LENGTH} characters (the real 'yyyy-mm-dd hh:mm:ss.0' form) and cannot be "
            "downgraded past 0012 without truncating source text. Export or delete those rows "
            "deliberately, or keep this migration applied."
        )
        raise RuntimeError(message)
    op.alter_column(
        "financial_fact_submission",
        "source_accepted",
        existing_type=sa.String(length=_NEW_LENGTH),
        type_=sa.String(length=_OLD_LENGTH),
        existing_nullable=True,
    )
