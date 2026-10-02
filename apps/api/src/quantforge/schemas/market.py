"""Daily price observation schemas."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from quantforge.schemas.common import as_utc_aware

__all__ = ["DailyPriceBarCreate", "DailyPriceBarRead"]

#: A price cannot be negative; the database says the same with
#: ``open_non_negative``/``high_non_negative``/``low_non_negative``/
#: ``close_non_negative``. There is deliberately no upper bound here. The
#: column is ``NUMERIC(20, 6)``, so the representable range is fixed by the
#: database, and an invented ceiling below it would reject legitimate data --
#: a 10^10 unit price is not a mistake, and a value past 10^14 minus the scale
#: fails at flush with a ``DataError`` naming the real limit.
_MIN_PRICE = Decimal("0")


class DailyPriceBarCreate(BaseModel):
    """Payload for storing one source's daily observation.

    ``observed_at`` and ``ingested_at`` are separate on purpose. ``observed_at``
    is the source's own "as of" and is optional because many daily feeds publish
    no publication timestamp. ``ingested_at`` defaults to now, which is the
    honest earliest-moment-known value when the source said nothing.

    ``adjusted_close`` is nullable and never defaulted to ``close``: a NULL
    means "this source published no adjusted close", which is materially
    different from "the adjusted close equals the close".
    """

    model_config = ConfigDict(extra="forbid")

    listing_id: UUID
    trading_date: date
    open: Decimal = Field(ge=_MIN_PRICE)
    high: Decimal = Field(ge=_MIN_PRICE)
    low: Decimal = Field(ge=_MIN_PRICE)
    close: Decimal = Field(ge=_MIN_PRICE)
    adjusted_close: Decimal | None = Field(default=None, ge=_MIN_PRICE)
    volume: int | None = Field(default=None, ge=0)
    source_id: UUID
    observed_at: datetime | None = None
    ingested_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _check_ohlc(self) -> DailyPriceBarCreate:
        """Enforce OHLC coherence before the database ever sees the row.

        All five implied bounds are checked so a rejection names the broken
        relationship rather than reporting a generic constraint failure.
        """
        if self.high < self.low:
            message = f"high ({self.high}) must be >= low ({self.low})."
            raise ValueError(message)
        if self.high < self.open:
            message = f"high ({self.high}) must be >= open ({self.open})."
            raise ValueError(message)
        if self.high < self.close:
            message = f"high ({self.high}) must be >= close ({self.close})."
            raise ValueError(message)
        if self.low > self.open:
            message = f"low ({self.low}) must be <= open ({self.open})."
            raise ValueError(message)
        if self.low > self.close:
            message = f"low ({self.low}) must be <= close ({self.close})."
            raise ValueError(message)
        return self

    @field_validator("observed_at")
    @classmethod
    def _check_observed_at(cls, value: datetime | None) -> datetime | None:
        """Require ``observed_at`` to be tz-aware when supplied."""
        return None if value is None else as_utc_aware(value)

    @field_validator("ingested_at")
    @classmethod
    def _check_ingested_at(cls, value: datetime) -> datetime:
        """Require ``ingested_at`` to be tz-aware."""
        return as_utc_aware(value)


class DailyPriceBarRead(BaseModel):
    """Read model for a stored observation.

    Prices stay ``Decimal``. In JSON mode Pydantic serialises ``Decimal`` as a
    *string* precisely so that a value like ``0.1`` cannot be silently
    re-parsed by a client as a binary float. That is intentional for a research
    system, and is covered by a unit test.
    """

    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    listing_id: UUID
    trading_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    adjusted_close: Decimal | None
    volume: int | None
    source_id: UUID
    observed_at: datetime | None
    ingested_at: datetime
    created_at: datetime
