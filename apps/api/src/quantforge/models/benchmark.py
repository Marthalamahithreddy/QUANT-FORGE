"""Benchmarks and their daily close levels.

A benchmark exists to give a security a reference for relative performance.
Future analytics derive abnormal returns from a benchmark's daily return, so
the bar series is stored as a first-class observation with full provenance
rather than pre-computed into some "excess return" column -- derived numbers
belong in the analytics layer, where the methodology can be changed and
re-run, not baked into the fact table.
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
    CURRENCY_LENGTH,
    DESCRIPTION_LENGTH,
    NAME_LENGTH,
    SHORT_NAME_LENGTH,
    CreatedAtMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    price_type,
    timestamptz,
    utcnow,
)
from quantforge.models.enums import PRICE_BASIS_LENGTH, PriceBasis, enum_check, enum_type

if TYPE_CHECKING:
    from quantforge.models.provenance import DataSource

__all__ = ["Benchmark", "BenchmarkPriceBar"]


class Benchmark(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A named reference index used for relative-performance analysis.

    Examples to be registered later: broad market indices, a sector index, or a
    rates benchmark. Nothing is seeded at this milestone.

    ``symbol`` is nullable and **not** unique. Benchmark naming is genuinely
    ambiguous in practice -- one logical index is published under several
    symbols by different vendors, and a vendor may re-use a symbol for a
    different index years later. Keying on a symbol would silently merge or
    conflate series, so identity is the UUID and the symbol is descriptive.

    The only index is on ``name``, supporting human lookup. There is no unique
    constraint by design; see above.
    """

    __tablename__ = "benchmark"
    __table_args__ = (
        sa.CheckConstraint(
            "symbol IS NULL OR symbol <> ''",
            name="symbol_not_blank",
        ),
        sa.CheckConstraint(
            "currency IS NULL OR currency ~ '^[A-Z]{3}$'",
            name="currency_iso4217",
        ),
        sa.Index("ix_benchmark_name", "name"),
    )

    name: Mapped[str] = mapped_column(
        sa.String(NAME_LENGTH),
        nullable=False,
        doc="Full name of the reference series.",
    )
    symbol: Mapped[str | None] = mapped_column(
        sa.String(SHORT_NAME_LENGTH),
        nullable=True,
        doc="Vendor/publisher symbol. Descriptive only; not unique.",
    )
    description: Mapped[str | None] = mapped_column(
        sa.String(DESCRIPTION_LENGTH),
        nullable=True,
        doc="Free-text description of what the series measures.",
    )
    currency: Mapped[str | None] = mapped_column(
        sa.String(CURRENCY_LENGTH),
        nullable=True,
        doc="ISO 4217 code the index level is quoted in, if applicable.",
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("data_source.id", ondelete="RESTRICT"),
        nullable=True,
        doc="Provider the benchmark definition came from, if attributed.",
    )
    is_active: Mapped[bool] = mapped_column(
        sa.Boolean,
        nullable=False,
        default=True,
        server_default=sa.true(),
        doc="False once the series is no longer used; retained for history.",
    )

    price_bars: Mapped[list[BenchmarkPriceBar]] = relationship(
        back_populates="benchmark",
        lazy="select",
        order_by="BenchmarkPriceBar.trading_date",
        doc="Daily close levels for this benchmark.",
    )

    def __repr__(self) -> str:
        return f"Benchmark(id={self.id!r}, name={self.name!r}, symbol={self.symbol!r})"


class BenchmarkPriceBar(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One source's daily close level for a benchmark.

    Mirrors :class:`~quantforge.models.market.DailyPriceBar`'s identity and
    provenance design:

    * ``UNIQUE (benchmark_id, trading_date, source_id, price_basis)`` -- one
      source-specific level per benchmark per session *per price basis*, so two
      disagreeing vendors both survive, one source re-reporting a date on the
      same basis is rejected, and a split-adjusted and an as-traded level for
      the same session coexist (see
      :class:`~quantforge.models.enums.PriceBasis`; research selects
      ``SPLIT_ADJUSTED`` explicitly).
    * ``observed_at`` (nullable, source's "as of") stays distinct from
      ``ingested_at`` (always present, when we stored it), for the same
      point-in-time reason.
    * Prices are exact ``Decimal`` values in ``NUMERIC``.

    Only ``close`` is stored. An index level is conventionally quoted as a
    single daily value, and abnormal-return maths needs the close-to-close
    return; carrying OHLC here would invite summing a level as if it were a
    traded price, which is meaningless. If intraday benchmark data is ever
    needed it belongs in a separate, explicitly time-granular model.

    As with price bars there is no ``updated_at``: this is a source fact.
    """

    __tablename__ = "benchmark_price_bar"
    __table_args__ = (
        sa.UniqueConstraint(
            "benchmark_id",
            "trading_date",
            "source_id",
            "price_basis",
            name="uq_benchmark_price_bar_benchmark_date_source_basis",
        ),
        enum_check("price_basis", PriceBasis),
        sa.CheckConstraint("close >= 0", name="close_non_negative"),
        sa.Index("ix_benchmark_price_bar_source_id", "source_id"),
    )

    benchmark_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("benchmark.id", ondelete="RESTRICT"),
        nullable=False,
        doc="Benchmark this level belongs to.",
    )
    trading_date: Mapped[date] = mapped_column(
        sa.Date,
        nullable=False,
        doc="Calendar date of the trading session (venue-local, DATE).",
    )
    close: Mapped[Decimal] = mapped_column(
        price_type(),
        nullable=False,
        doc="Index close level for the session, as reported by the source.",
    )
    price_basis: Mapped[PriceBasis] = mapped_column(
        enum_type(PriceBasis, name="price_basis", length=PRICE_BASIS_LENGTH),
        nullable=False,
        doc=(
            "Split-adjustment basis of this level: SPLIT_ADJUSTED (research) or "
            "AS_TRADED. Required; part of the uniqueness key."
        ),
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("data_source.id", ondelete="RESTRICT"),
        nullable=False,
        doc="Provider that reported this level.",
    )
    observed_at: Mapped[datetime | None] = mapped_column(
        timestamptz(),
        nullable=True,
        doc=(
            "When the source indicated the level became available, if known. NULL "
            "means undisclosed, not 'equal to ingested_at'."
        ),
    )
    ingested_at: Mapped[datetime] = mapped_column(
        timestamptz(),
        nullable=False,
        default=utcnow,
        doc="When QuantForge received and stored this level (UTC).",
    )

    benchmark: Mapped[Benchmark] = relationship(
        back_populates="price_bars",
        lazy="joined",
    )
    source: Mapped[DataSource] = relationship(
        back_populates="benchmark_price_bars",
        lazy="joined",
    )

    def __repr__(self) -> str:
        return f"BenchmarkPriceBar(benchmark_id={self.benchmark_id!r}, date={self.trading_date!r})"
