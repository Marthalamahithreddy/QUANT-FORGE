"""Historical share-count events (stock splits) and the coverage that proves their absence.

Two facts, kept apart on purpose:

``StockSplit``
    "This security's share count changed by ``old_shares`` -> ``new_shares``,
    effective on ``execution_date``", as a source stated it. It attaches to the
    **security** (the share class the corporate action economically applies to),
    never to a ticker, a listing or the company: tickers change and are reused,
    and one issuer can have several share classes. The provider identifies a
    split through a ticker, which ingestion resolves to a security through the
    listing valid on ``execution_date`` (half-open ``[valid_from, valid_to)``),
    never through today's ticker.

``StockSplitCoverage``
    "For this security, between ``covered_from`` and ``covered_to`` (inclusive)
    a complete split fetch ran and every record in it was stored." An empty
    ``stock_split`` table does NOT mean no split happened -- it may mean none was
    ever fetched. Only a coverage row lets "no split found" be read as "no split
    existed", and only for the dates it covers.

Semantics (Massive, verified against its documentation)
-------------------------------------------------------
* ``old_shares`` / ``new_shares`` are Massive's ``split_from`` / ``split_to``: a
  2-for-1 split is old=1, new=2; a 1-for-10 reverse split is old=10, new=1. The
  share count is multiplied by ``new_shares / old_shares``. Both are exact
  ``NUMERIC`` (a stock dividend can be 1 -> 1.027) with no invented upper bound.
* ``execution_date`` is when the split takes effect: the prior session's
  post-market is the last to show pre-split prices; on ``execution_date`` all
  trading, pre-market included, is already adjusted. It is a calendar date and
  is **not** an availability time -- Massive publishes no announcement or
  publication timestamp, so none is stored and none is synthesised.
* ``adjustment_type`` is the provider's classification. Nothing here adjusts a
  price, a share count or a cost basis; the table only records that the event
  exists.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from quantforge.db.base import Base
from quantforge.models.base import CreatedAtMixin, UUIDPrimaryKeyMixin, timestamptz, utcnow
from quantforge.models.enums import (
    SPLIT_ADJUSTMENT_TYPE_LENGTH,
    StockSplitAdjustmentType,
    enum_check,
    enum_type,
)

__all__ = ["PROVIDER_SPLIT_ID_LENGTH", "StockSplit", "StockSplitCoverage"]

#: Massive's split ids are a letter plus 64 hex characters; the column is wider so
#: another provider's id fits, and still bounded so a runaway value cannot.
PROVIDER_SPLIT_ID_LENGTH = 128
PROVIDER_TICKER_LENGTH = 32


class StockSplit(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One provider-stated share-count event for one security. See the module docstring."""

    __tablename__ = "stock_split"
    __table_args__ = (
        sa.UniqueConstraint(
            "source_id", "provider_split_id", name="uq_stock_split_source_provider_split_id"
        ),
        enum_check("adjustment_type", StockSplitAdjustmentType),
        sa.CheckConstraint("old_shares > 0", name="old_shares_positive"),
        sa.CheckConstraint("new_shares > 0", name="new_shares_positive"),
        sa.Index("ix_stock_split_security_id_execution_date", "security_id", "execution_date"),
        sa.Index("ix_stock_split_source_id", "source_id"),
        sa.Index("ix_stock_split_ingestion_run_id", "ingestion_run_id"),
    )

    security_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("security.id", ondelete="RESTRICT"),
        nullable=False,
        doc="The share class whose share count changed (resolved historically, never by ticker).",
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("data_source.id", ondelete="RESTRICT"),
        nullable=False,
        doc="Provider that reported the split.",
    )
    provider_split_id: Mapped[str] = mapped_column(
        sa.String(PROVIDER_SPLIT_ID_LENGTH),
        nullable=False,
        doc="The provider's own identifier for this split: the external identity.",
    )
    provider_ticker: Mapped[str] = mapped_column(
        sa.String(PROVIDER_TICKER_LENGTH),
        nullable=False,
        doc=(
            "The symbol the provider reported the split under, kept for audit only. It is "
            "NOT the identity: security_id is."
        ),
    )
    execution_date: Mapped[date] = mapped_column(
        sa.Date,
        nullable=False,
        doc=(
            "Calendar date the split takes effect (all trading on it is already adjusted). "
            "Not an availability time."
        ),
    )
    old_shares: Mapped[Decimal] = mapped_column(
        sa.Numeric,
        nullable=False,
        doc="Shares before (Massive split_from). A 2-for-1 split has old_shares=1.",
    )
    new_shares: Mapped[Decimal] = mapped_column(
        sa.Numeric,
        nullable=False,
        doc="Shares after (Massive split_to). A 2-for-1 split has new_shares=2.",
    )
    adjustment_type: Mapped[StockSplitAdjustmentType] = mapped_column(
        enum_type(
            StockSplitAdjustmentType,
            name="stock_split_adjustment_type",
            length=SPLIT_ADJUSTMENT_TYPE_LENGTH,
        ),
        nullable=False,
        doc="The provider's classification of the share-count event.",
    )
    ingestion_run_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("ingestion_run.id", ondelete="RESTRICT"),
        nullable=True,
        doc="Run that wrote this row; set on insert, never rewritten.",
    )
    ingested_at: Mapped[datetime] = mapped_column(
        timestamptz(),
        nullable=False,
        default=utcnow,
        doc="When QuantForge stored this split (UTC). Not availability evidence.",
    )

    def __repr__(self) -> str:
        return (
            f"StockSplit(security_id={self.security_id!r}, date={self.execution_date!r}, "
            f"{self.old_shares}->{self.new_shares})"
        )


class StockSplitCoverage(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """A security-and-date range for which a complete split fetch succeeded.

    Written only by a run that fetched every page, resolved every record to this
    security and stored them all (any rejected, unresolved or ambiguous record
    fails the run and writes nothing). ``covered_to`` is always at least
    seven days before the day of the fetch, because a provider's answer about the
    future -- or about the last few days, before every record is published -- is not
    evidence that nothing happened. The range is clamped to the dates the security
    actually held the queried ticker, and is recorded only through the security's
    LATEST ticker: whether the provider indexes a retired symbol is unverified, and an
    empty answer for one must never become coverage.
    """

    __tablename__ = "stock_split_coverage"
    __table_args__ = (
        sa.UniqueConstraint(
            "source_id",
            "security_id",
            "covered_from",
            "covered_to",
            name="uq_stock_split_coverage_range",
        ),
        sa.CheckConstraint("covered_from <= covered_to", name="range_ordered"),
        sa.Index("ix_stock_split_coverage_security_id", "security_id"),
        sa.Index("ix_stock_split_coverage_ingestion_run_id", "ingestion_run_id"),
    )

    security_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("security.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("data_source.id", ondelete="RESTRICT"),
        nullable=False,
    )
    provider_ticker: Mapped[str] = mapped_column(
        sa.String(PROVIDER_TICKER_LENGTH),
        nullable=False,
        doc="The symbol that was queried, for audit.",
    )
    covered_from: Mapped[date] = mapped_column(sa.Date, nullable=False, doc="Inclusive.")
    covered_to: Mapped[date] = mapped_column(sa.Date, nullable=False, doc="Inclusive.")
    ingestion_run_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("ingestion_run.id", ondelete="RESTRICT"),
        nullable=True,
        doc="The run whose complete fetch backs this claim.",
    )

    def __repr__(self) -> str:
        return (
            f"StockSplitCoverage(security_id={self.security_id!r}, "
            f"{self.covered_from}..{self.covered_to})"
        )
