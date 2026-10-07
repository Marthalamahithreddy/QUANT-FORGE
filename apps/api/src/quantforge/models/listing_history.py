"""Sourced historical listing facts: what a provider STATES, one date at a time.

``Listing.valid_from`` records when QuantForge FIRST OBSERVED a listing (see
``quantforge.ingestion.reference``, "valid_from policy"); it is operational, provisional
knowledge and proves nothing about earlier dates. This table is where historical claims
live instead, each with its source and the run that stored it, so that

    "I first observed this listing on date X"

stays distinguishable from

    "source S states this ticker/security relationship held on date D".

A fact is a statement about a single date (see :class:`~quantforge.models.enums.ListingFactKind`);
no interval is stored. Intervals are DERIVED by :mod:`quantforge.identity.listing_history`, a pure,
deterministic function of the facts and the provisional listings, so a re-run can never differ
and nothing is rewritten. Facts attach to a **security** (resolved through the share-class FIGI),
never to a bare ticker or to the company (CIK is registrant identity, not security identity).

``mic`` is a nullable venue column allowed only on ``TICKER_OBSERVED`` (CHECK
``mic_only_on_observation``). No current writer fills it: a dated snapshot's primary exchange is
NOT documented by the provider as of the requested date, so it is not persisted (it stays in the raw
response only) and is never used as venue evidence by the resolver. Ticker-change events say nothing
about venues either, so no exchange continuity is ever implied, and complete exchange-transfer
history is NOT established.

Like the other fact tables there is no ``updated_at``: a fact is stated once, never edited.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from quantforge.db.base import Base
from quantforge.models.base import CreatedAtMixin, UUIDPrimaryKeyMixin, timestamptz, utcnow
from quantforge.models.enums import (
    LISTING_FACT_KIND_LENGTH,
    ListingFactKind,
    enum_check,
    enum_type,
)

__all__ = ["FACT_TICKER_LENGTH", "ListingHistoryFact"]

FACT_TICKER_LENGTH = 32
FACT_MIC_LENGTH = 4


class ListingHistoryFact(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One provider-stated, single-date fact about a security's ticker. See the module docstring."""

    __tablename__ = "listing_history_fact"
    __table_args__ = (
        sa.UniqueConstraint(
            "source_id",
            "security_id",
            "kind",
            "ticker",
            "effective_date",
            name="uq_listing_history_fact_identity",
        ),
        enum_check("kind", ListingFactKind),
        sa.CheckConstraint("ticker = upper(ticker) AND ticker <> ''", name="ticker_canonical"),
        # Calendar dates only, in a sane range: a nonsense date can neither overflow the derivation's
        # day arithmetic nor manufacture a boundary.
        sa.CheckConstraint(
            "effective_date BETWEEN DATE '1900-01-01' AND DATE '2100-12-31'",
            name="effective_date_sane_range",
        ),
        sa.CheckConstraint(
            "mic IS NULL OR kind = 'TICKER_OBSERVED'", name="mic_only_on_observation"
        ),
        sa.Index("ix_listing_history_fact_security_id", "security_id"),
        sa.Index("ix_listing_history_fact_ticker", "ticker"),
        sa.Index("ix_listing_history_fact_source_id", "source_id"),
        sa.Index("ix_listing_history_fact_ingestion_run_id", "ingestion_run_id"),
    )

    security_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("security.id", ondelete="RESTRICT"),
        nullable=False,
        doc="The share class the fact is about (resolved through its share-class FIGI).",
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("data_source.id", ondelete="RESTRICT"),
        nullable=False,
        doc="The provider that stated the fact. Facts from different sources coexist; none wins.",
    )
    kind: Mapped[ListingFactKind] = mapped_column(
        enum_type(ListingFactKind, name="listing_fact_kind", length=LISTING_FACT_KIND_LENGTH),
        nullable=False,
        doc="What the fact states; its date meaning is defined per kind.",
    )
    ticker: Mapped[str] = mapped_column(
        sa.String(FACT_TICKER_LENGTH),
        nullable=False,
        doc="The symbol, upper-case.",
    )
    effective_date: Mapped[date] = mapped_column(
        sa.Date,
        nullable=False,
        doc=(
            "The ONE calendar date the fact is about: the observation date, the first date of the "
            "ticker, or the last traded date, per `kind`."
        ),
    )
    mic: Mapped[str | None] = mapped_column(
        sa.String(FACT_MIC_LENGTH),
        nullable=True,
        doc=(
            "Reserved venue MIC (observations only). Always NULL from snapshots: the provider's "
            "primary exchange is not documented as of the requested date, so it is not stored."
        ),
    )
    ingestion_run_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("ingestion_run.id", ondelete="RESTRICT"),
        nullable=True,
        doc="Run that stored this fact; set on insert, never rewritten.",
    )
    ingested_at: Mapped[datetime] = mapped_column(
        timestamptz(),
        nullable=False,
        default=utcnow,
        doc="When QuantForge stored the fact (UTC). Reference-data provenance, not availability evidence.",
    )

    def __repr__(self) -> str:
        return (
            f"ListingHistoryFact(security_id={self.security_id!r}, kind={self.kind!r}, "
            f"ticker={self.ticker!r}, date={self.effective_date!r})"
        )
