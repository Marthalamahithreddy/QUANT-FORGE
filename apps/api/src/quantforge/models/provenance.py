"""Data provenance: where a number or event came from.

Every observation and corporate event in this schema carries a ``source_id``
foreign key, and the reference is mandatory wherever the fact originates from a
provider. Provenance is not decorative metadata in a research system: it is what
makes a backtest reproducible, lets a later reconciliation layer prefer one
vendor over another, and answers "which source published this, and when could we
have known it?".

No rows are seeded here. Rows arrive when an ingestion pipeline is introduced,
and each is registered deliberately so the source's licensing and reliability
characteristics stay a conscious decision rather than a side effect of an import.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from quantforge.db.base import Base
from quantforge.models.base import (
    SHORT_NAME_LENGTH,
    URL_LENGTH,
    CreatedAtMixin,
    UUIDPrimaryKeyMixin,
)
from quantforge.models.enums import (
    PROVIDER_TYPE_LENGTH,
    ProviderType,
    enum_check,
    enum_type,
)

if TYPE_CHECKING:
    # Imported for type checking only. The real import happens in
    # ``quantforge.models.__init__``; SQLAlchemy resolves these names from the
    # class registry when it configures the mappers, which is what allows
    # ``DataSource`` to declare a back-reference to three tables that import it.
    # A runtime import here would be circular.
    from quantforge.models.benchmark import BenchmarkPriceBar
    from quantforge.models.earnings import EarningsEvent
    from quantforge.models.market import DailyPriceBar

__all__ = ["DataSource"]


class DataSource(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """A provider QuantForge can attribute stored facts to.

    Examples to be registered later: a regulatory filing system, an exchange's
    own feed, or a commercial market-data vendor. Nothing is seeded at this
    milestone.

    ``name`` is unique because it is the human handle used in audits and
    ingestion config; two distinct providers sharing a name would make a
    provenance column ambiguous. The uniqueness is on the *label*, not on
    ``base_url``: two products can share a vendor's domain while being
    different feeds, and the same feed can be reached on several domains.
    """

    __tablename__ = "data_source"
    __table_args__ = (
        enum_check("provider_type", ProviderType),
        sa.CheckConstraint(
            "base_url IS NULL OR base_url ~ '^https?://'",
            name="base_url_scheme",
        ),
    )

    name: Mapped[str] = mapped_column(
        sa.String(SHORT_NAME_LENGTH),
        nullable=False,
        unique=True,
        doc="Unique human-readable label for this source.",
    )
    provider_type: Mapped[ProviderType] = mapped_column(
        enum_type(ProviderType, name="provider_type", length=PROVIDER_TYPE_LENGTH),
        nullable=False,
        doc="Class of organisation behind the source, for trust/licensing policy.",
    )
    base_url: Mapped[str | None] = mapped_column(
        sa.String(URL_LENGTH),
        nullable=True,
        doc="Document or endpoint root, http(s) only; null for offline sources.",
    )
    is_active: Mapped[bool] = mapped_column(
        sa.Boolean,
        nullable=False,
        default=True,
        server_default=sa.true(),
        doc="False once a source is retired; rows are kept so history stays intact.",
    )

    price_bars: Mapped[list[DailyPriceBar]] = relationship(
        back_populates="source",
        doc="Observations attributed to this source, for provenance audits.",
    )
    benchmark_price_bars: Mapped[list[BenchmarkPriceBar]] = relationship(
        back_populates="source",
        doc="Benchmark observations attributed to this source.",
    )
    earnings_events: Mapped[list[EarningsEvent]] = relationship(
        back_populates="source",
        doc="Corporate events attributed to this source.",
    )

    def __repr__(self) -> str:
        return f"DataSource(id={self.id!r}, name={self.name!r})"
