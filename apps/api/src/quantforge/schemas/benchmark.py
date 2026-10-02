"""Benchmark and benchmark level schemas."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from quantforge.models.base import (
    CURRENCY_LENGTH,
    DESCRIPTION_LENGTH,
    NAME_LENGTH,
    SHORT_NAME_LENGTH,
)
from quantforge.schemas.common import (
    CodeValidatingSchema,
    OrmReadSchema,
    as_utc_aware,
    blank_to_none,
)

__all__ = ["BenchmarkCreate", "BenchmarkPriceBarCreate", "BenchmarkPriceBarRead", "BenchmarkRead"]

#: An index level cannot be negative, matching the database's
#: ``close_non_negative`` check. No upper bound is imposed: the column is
#: ``NUMERIC(20, 6)``, and a synthetic or rebased index can legitimately print a
#: level well past any figure picked in advance. The database's precision and
#: scale are the authority, and they report the real limit if one is exceeded.
_MIN_INDEX_LEVEL = Decimal("0")


class BenchmarkCreate(CodeValidatingSchema):
    """Payload for registering a reference series.

    ``symbol`` is optional and not unique. One logical index is published under
    several vendor symbols, and a symbol can be re-used for a different index
    years later, so keying on it would merge unrelated series.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=NAME_LENGTH)
    symbol: str | None = Field(default=None, max_length=SHORT_NAME_LENGTH)
    description: str | None = Field(default=None, max_length=DESCRIPTION_LENGTH)
    currency: str | None = Field(default=None, max_length=CURRENCY_LENGTH)
    source_id: UUID | None = None
    is_active: bool = True

    @field_validator("symbol", "description", mode="before")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        """Normalise empty source strings to ``None``."""
        return blank_to_none(value)


class BenchmarkRead(OrmReadSchema):
    """Read model for a benchmark series."""

    id: UUID
    name: str
    symbol: str | None
    description: str | None
    currency: str | None
    source_id: UUID | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class BenchmarkPriceBarCreate(BaseModel):
    """Payload for storing one source's daily benchmark level.

    ``observed_at``/``ingested_at`` follow the same discipline as security price
    bars: the source's "as of" is optional and distinct from when we stored it.
    """

    model_config = ConfigDict(extra="forbid")

    benchmark_id: UUID
    trading_date: date
    close: Decimal = Field(ge=_MIN_INDEX_LEVEL)
    source_id: UUID
    observed_at: datetime | None = None
    ingested_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

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

    @model_validator(mode="after")
    def _check_single_level(self) -> BenchmarkPriceBarCreate:
        """Placeholder for future cross-field rules.

        Only ``close`` is stored for a benchmark, so there is no OHLC coherence
        to verify. This hook exists so the validator set is explicit and a future
        rule has an obvious home.
        """
        return self


class BenchmarkPriceBarRead(BaseModel):
    """Read model for a stored benchmark level; ``close`` stays ``Decimal``."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    benchmark_id: UUID
    trading_date: date
    close: Decimal
    source_id: UUID
    observed_at: datetime | None
    ingested_at: datetime
    created_at: datetime
