"""Make ``sec_filing.accepted_at`` nullable (acceptance instant not established).

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-07

Step 11.2H. The submissions-JSON ``acceptanceDateTime`` has no documented timezone and was
observed to disagree with the official EDGAR header (docs/sec-acceptance-time-design.md), so bulk
ingestion stores filing IDENTITY without an acceptance instant; ``accepted_at`` is set only from the
EDGAR header, on demand, per accession. NULL means "the exact acceptance instant has not been
established from the approved header source". It does not mean the filing is invalid or unavailable.

Upgrade drops ``NOT NULL`` only: every stored value is preserved, nothing is rewritten or
backfilled. Downgrade refuses while any row has a NULL ``accepted_at``: restoring ``NOT NULL`` would
otherwise require inventing an instant (or deleting a valid filing), and neither is done.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0014"
down_revision: str | Sequence[str] | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        "sec_filing",
        "accepted_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=True,
    )


def downgrade() -> None:
    """Downgrade schema.

    Refuses if any filing has no established acceptance instant; no instant is invented and no row
    is deleted.
    """
    if op.get_context().as_sql:
        message = (
            "the NULL accepted_at check needs a live database connection; run this downgrade "
            "online, not with --sql"
        )
        raise RuntimeError(message)
    unestablished = (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM sec_filing WHERE accepted_at IS NULL"))
        .scalar_one()
    )
    if unestablished:
        message = (
            f"{unestablished} sec_filing row(s) have no established accepted_at and cannot be "
            "downgraded past 0014 without inventing an acceptance instant. Establish them from the "
            "EDGAR header (sec-filing-acceptance) or remove those rows deliberately, or keep this "
            "migration applied."
        )
        raise RuntimeError(message)
    op.alter_column(
        "sec_filing",
        "accepted_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
    )
