"""Explicit price basis on daily and benchmark price bars.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-02

Adds ``price_basis`` (``VARCHAR(32)``, ``NOT NULL``, ``CHECK`` in
``SPLIT_ADJUSTED``/``AS_TRADED``) to both ``daily_price_bar`` and
``benchmark_price_bar``, and widens each table's uniqueness key to include it.

Why
---
Massive's ``adjusted=true`` is split-adjusted (dividends excluded); that is the
research basis. A historical execution/accounting model needs the as-traded
(``adjusted=false``) prices. The previous keys
``(listing_id, trading_date, source_id)`` and
``(benchmark_id, trading_date, source_id)`` could not hold both bases for one
session, and would have misreported a legitimate second basis as corrupted
provider data. ``adjusted_close`` is NOT repurposed: it stays unconditionally
NULL, and each row carries exactly one basis for all of its price fields.

Backfill
--------
Every existing row in either table is labelled ``SPLIT_ADJUSTED``. That is a
fact established from the repository, not an assumption: the only code that has
ever written either table is the Massive ingestion path
(``MassiveClient.get_daily_bars``), which has hard-coded ``adjusted=true`` since
it was introduced in the first commit, and no API or other writer exists. The
column is added nullable, backfilled, then made NOT NULL with no server
default, so the database never again invents a basis for a writer that forgot
to state one.

Downgrade
---------
Restoring the old keys is impossible once a session holds both bases, and
silently deleting the ``AS_TRADED`` rows would destroy data. The downgrade
therefore refuses while any ``AS_TRADED`` row exists and says what to do.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BASES = ("SPLIT_ADJUSTED", "AS_TRADED")

#: table, natural-key leading column, old and new unique constraint names.
_TABLES = (
    (
        "daily_price_bar",
        "listing_id",
        "uq_daily_price_bar_listing_date_source",
        "uq_daily_price_bar_listing_date_source_basis",
    ),
    (
        "benchmark_price_bar",
        "benchmark_id",
        "uq_benchmark_price_bar_benchmark_date_source",
        "uq_benchmark_price_bar_benchmark_date_source_basis",
    ),
)


def upgrade() -> None:
    """Upgrade schema."""
    literals = ", ".join(f"'{basis}'" for basis in _BASES)
    for table, owner, old_unique, new_unique in _TABLES:
        # Nullable first so the backfill can label every existing row.
        op.add_column(table, sa.Column("price_basis", sa.String(length=32), nullable=True))
        op.execute(f"UPDATE {table} SET price_basis = 'SPLIT_ADJUSTED' WHERE price_basis IS NULL")
        op.alter_column(table, "price_basis", nullable=False)
        op.create_check_constraint(
            op.f(f"ck_{table}_price_basis_enum_values"),
            table,
            f"price_basis IN ({literals})",
        )
        op.drop_constraint(op.f(old_unique), table, type_="unique")
        op.create_unique_constraint(
            op.f(new_unique), table, [owner, "trading_date", "source_id", "price_basis"]
        )


def downgrade() -> None:
    """Downgrade schema.

    Refuses if any ``AS_TRADED`` row exists: the old uniqueness keys cannot hold
    two bases for one session, and dropping the rows would lose data.
    """
    if op.get_context().as_sql:
        message = (
            "the AS_TRADED safety check needs a live database connection; run this "
            "downgrade online, not with --sql"
        )
        raise RuntimeError(message)
    connection = op.get_bind()
    for table, _owner, _old_unique, _new_unique in _TABLES:
        as_traded = connection.execute(
            sa.text(f"SELECT count(*) FROM {table} WHERE price_basis = 'AS_TRADED'")  # noqa: S608
        ).scalar_one()
        if as_traded:
            message = (
                f"{as_traded} {table} row(s) have price_basis AS_TRADED and cannot be "
                "downgraded past 0006: the previous uniqueness key cannot hold two "
                "price bases for one session. Export or delete those rows deliberately "
                "before downgrading, or keep this migration applied."
            )
            raise RuntimeError(message)

    for table, owner, old_unique, new_unique in _TABLES:
        op.drop_constraint(op.f(new_unique), table, type_="unique")
        op.create_unique_constraint(op.f(old_unique), table, [owner, "trading_date", "source_id"])
        op.drop_constraint(op.f(f"ck_{table}_price_basis_enum_values"), table, type_="check")
        op.drop_column(table, "price_basis")
