"""Daily market observations for a listed instrument.

This is the fact table the research layer will be built on, so its grain and
its invariants are the most important decisions in the schema.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from quantforge.db.base import Base
from quantforge.models.base import (
    CreatedAtMixin,
    UUIDPrimaryKeyMixin,
    price_type,
    timestamptz,
    utcnow,
)
from quantforge.models.enums import PRICE_BASIS_LENGTH, PriceBasis, enum_check, enum_type

if TYPE_CHECKING:
    from quantforge.models.ingestion import IngestionRun
    from quantforge.models.provenance import DataSource
    from quantforge.models.security import Listing

__all__ = ["DailyPriceBar"]

#: ISO 8601 calendar date of the trading session the bar describes. A ``DATE``,
#: not a timestamp: "the session of 2024-03-15" has no time or zone of its own,
#: and the venue's timezone comes from ``Exchange.timezone`` via the listing.
TRADING_DATE_DOC = "Calendar date of the trading session (venue-local, DATE)."


class DailyPriceBar(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One venue's daily OHLCV observation for one instrument on one date.

    Grain and identity
    ------------------
    The row is "what *this source* reported about *this listing* on *this
    trading date*, expressed in *this price basis*", which is why the
    uniqueness key is ``(listing_id, trading_date, source_id, price_basis)``
    and not ``(listing_id, trading_date)``.

    Price basis
    -----------
    ``price_basis`` (:class:`~quantforge.models.enums.PriceBasis`) says whether
    the row's prices are ``SPLIT_ADJUSTED`` (the research basis) or
    ``AS_TRADED`` (the execution/accounting candidate). A row has exactly one
    basis and *all* of its price fields belong to it, so the same session can
    hold one row per basis without either redefining the other. It is NOT NULL
    with no default: a writer must state the basis. Any query that does not
    care about the distinction is therefore a bug -- research selects
    ``SPLIT_ADJUSTED`` explicitly.

    Two consequences of the source-aware key follow, and both are intended:

    * Two vendors disagreeing about the same session produce two rows rather
      than a silent overwrite. Preserving the disagreement is what lets a later
      reconciliation layer choose a source, measure vendor error, and avoid
      look-ahead bias by preferring a specific provider.
    * The same source reporting the same session twice is rejected, which
      blocks the common double-ingestion bug.

    The listing, not the security, is the observation subject. The same
    security observed on two venues produces two independent bar series, which
    is required for any analysis of cross-venue price dislocations.

    Exact decimals
    --------------
    Prices are ``NUMERIC(20, 6)`` and are handled as ``Decimal`` end to end.
    A ``float`` would make 0.1 + 0.2 != 0.3 in financial comparisons, and
    binary floating point cannot represent most decimal fractions exactly --
    unacceptable for price levels that are compared and differenced. The type
    also widens deliberately: 20 total digits with 6 decimal places covers
    sub-cent instruments as well as un-adjusted magnitudes.

    ``adjusted_close`` is nullable and is deliberately **not** treated as
    equivalent to ``close``. It is not populated by QuantForge's ingestion
    (it is always NULL today) and its adjustment methodology is not defined by
    any current provider contract. It must not be used to carry a second
    price basis -- that is what ``price_basis`` rows are for. A NULL here
    unambiguously means "no adjusted close was supplied", never "adjusted
    close equals close".

    Observation time vs. ingestion time
    ----------------------------------
    ``observed_at`` and ``ingested_at`` answer different questions and are never
    merged:

    * ``observed_at`` -- when the *source* indicated this fact became available
      ("as of"), if it said. Nullable, because plenty of sources publish a daily
      bar with no publication timestamp at all. A NULL means genuinely unknown,
      which is different from "equals ingestion time".
    * ``ingested_at`` -- when *QuantForge* received and stored it. Always
      populated. This is the earliest moment a backtest could have used the
      fact, so it is the value that makes a simulation honest.

    A point-in-time query must use ``COALESCE(observed_at, ingested_at)`` to get
    "when could we have known this"; using ``ingested_at`` alone is the
    conservative choice and ``observed_at`` alone would silently drop the rows
    whose publication time the source never disclosed.

    Immutable by design
    -------------------
    There is no ``updated_at``: a price bar is a source fact, and a correction
    arrives as a new source-attributed row or a new ``ingested_at`` revision,
    not as an in-place edit that rewrites history.
    """

    __tablename__ = "daily_price_bar"
    __table_args__ = (
        sa.UniqueConstraint(
            "listing_id",
            "trading_date",
            "source_id",
            "price_basis",
            name="uq_daily_price_bar_listing_date_source_basis",
        ),
        enum_check("price_basis", PriceBasis),
        # Prices are non-negative. A negative "price" is always a sign or unit
        # error in the feed; zero is retained because a placeholder/halted
        # session legitimately reports 0.00.
        sa.CheckConstraint("open >= 0", name="open_non_negative"),
        sa.CheckConstraint("high >= 0", name="high_non_negative"),
        sa.CheckConstraint("low >= 0", name="low_non_negative"),
        sa.CheckConstraint("close >= 0", name="close_non_negative"),
        sa.CheckConstraint(
            "adjusted_close IS NULL OR adjusted_close >= 0",
            name="adjusted_close_non_negative",
        ),
        # OHLC coherence. The four implied bounds (high >= open, high >= close,
        # low <= open, low <= close) plus high >= low is the full internal
        # consistency requirement; all five are stated explicitly so a rejected
        # row names the broken relationship.
        sa.CheckConstraint("high >= low", name="high_ge_low"),
        sa.CheckConstraint("high >= open", name="high_ge_open"),
        sa.CheckConstraint("high >= close", name="high_ge_close"),
        sa.CheckConstraint("low <= open", name="low_le_open"),
        sa.CheckConstraint("low <= close", name="low_le_close"),
        # Volume is a share/lot count: integral, never negative. Nullable
        # because some feeds genuinely omit it.
        sa.CheckConstraint("volume IS NULL OR volume >= 0", name="volume_non_negative"),
        sa.CheckConstraint("trades IS NULL OR trades >= 0", name="trades_non_negative"),
        sa.CheckConstraint("vwap IS NULL OR vwap > 0", name="vwap_positive"),
        # Provenance audit: "which rows did this source contribute?"
        sa.Index("ix_daily_price_bar_source_id", "source_id"),
        # "Which run wrote this row?" A timestamp cannot answer that -- two runs
        # can share a clock, and an injected test clock makes that the norm rather
        # than the exception -- so the link is a real foreign key.
        sa.Index("ix_daily_price_bar_ingestion_run_id", "ingestion_run_id"),
    )

    listing_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("listing.id", ondelete="RESTRICT"),
        nullable=False,
        doc="Listing whose price series this observation belongs to.",
    )
    trading_date: Mapped[date] = mapped_column(sa.Date, nullable=False, doc=TRADING_DATE_DOC)
    open: Mapped[Decimal] = mapped_column(price_type(), nullable=False, doc="Session open price.")
    high: Mapped[Decimal] = mapped_column(price_type(), nullable=False, doc="Session high price.")
    low: Mapped[Decimal] = mapped_column(price_type(), nullable=False, doc="Session low price.")
    close: Mapped[Decimal] = mapped_column(price_type(), nullable=False, doc="Session close price.")
    adjusted_close: Mapped[Decimal | None] = mapped_column(
        price_type(),
        nullable=True,
        doc=(
            "Reserved; not populated by QuantForge ingestion (always NULL). Its "
            "adjustment methodology is not defined by any provider contract and "
            "it never carries a second price basis -- see price_basis. NULL means "
            "'not provided' and is never equivalent to close."
        ),
    )
    price_basis: Mapped[PriceBasis] = mapped_column(
        enum_type(PriceBasis, name="price_basis", length=PRICE_BASIS_LENGTH),
        nullable=False,
        doc=(
            "Split-adjustment basis of every price field on this row: "
            "SPLIT_ADJUSTED (research) or AS_TRADED (execution/accounting "
            "candidate). Required; part of the uniqueness key."
        ),
    )
    volume: Mapped[int | None] = mapped_column(
        sa.BigInteger,
        nullable=True,
        doc="Session volume in shares or lots, as reported. Never negative.",
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("data_source.id", ondelete="RESTRICT"),
        nullable=False,
        doc="Provider that reported this observation.",
    )
    vwap: Mapped[Decimal | None] = mapped_column(
        sa.Numeric(20, 6),
        nullable=True,
        doc=(
            "Provider-reported volume-weighted average price. Nullable because "
            "not every source publishes one. Deliberately stored as reported "
            "rather than recomputed from OHLCV: a stored value that always "
            "reproduces the vendor's own figure is auditable, whereas a "
            "recomputed one silently disagrees with the vendor's methodology "
            "and nobody can tell which is which."
        ),
    )
    trades: Mapped[int | None] = mapped_column(
        sa.BigInteger,
        nullable=True,
        doc=(
            "Provider-reported trade count for the session. Nullable because "
            "not every source publishes one. Carried through as a liquidity "
            "proxy; a session with real volume and no trades would otherwise be "
            "indistinguishable from an untraded one."
        ),
    )
    observed_at: Mapped[datetime | None] = mapped_column(
        timestamptz(),
        nullable=True,
        doc=(
            "When the source indicated the fact became available, if known. NULL "
            "means the source disclosed no publication time; it does not mean "
            "'equal to ingested_at'."
        ),
    )
    ingestion_run_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("ingestion_run.id", ondelete="RESTRICT"),
        nullable=True,
        doc=(
            "Run that wrote this row. Nullable so a bar can be loaded outside a "
            "recorded run (fixtures, backfills by hand); NULL then honestly means "
            "'no run explains this', which is itself the answer a reconciliation "
            "query is looking for. Set on insert, never rewritten: a correction "
            "arrives as a new row under a new run, so this stays the row's origin."
        ),
    )
    ingested_at: Mapped[datetime] = mapped_column(
        timestamptz(),
        nullable=False,
        default=utcnow,
        doc="When QuantForge received and stored this observation (UTC).",
    )

    listing: Mapped[Listing] = relationship(back_populates="price_bars", lazy="joined")
    source: Mapped[DataSource] = relationship(
        back_populates="price_bars",
        lazy="joined",
    )
    ingestion_run: Mapped[IngestionRun | None] = relationship(
        back_populates="price_bars",
        lazy="joined",
    )

    def __repr__(self) -> str:
        return f"DailyPriceBar(listing_id={self.listing_id!r}, trading_date={self.trading_date!r})"
