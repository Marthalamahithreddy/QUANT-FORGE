"""Earnings event schemas."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import ConfigDict, Field, field_validator, model_validator

from quantforge.models.base import (
    CURRENCY_LENGTH,
    MAX_FISCAL_QUARTER,
    MAX_FISCAL_YEAR,
    MIN_FISCAL_QUARTER,
    MIN_FISCAL_YEAR,
)
from quantforge.models.enums import AvailabilityBasis, EarningsTiming
from quantforge.schemas.common import CodeValidatingSchema, OrmReadSchema, as_utc_aware

__all__ = ["EarningsEventCreate", "EarningsEventRead"]

#: EPS and revenue magnitudes. Revenue for a large issuer runs into the hundreds
#: of billions; EPS is small but a multi-class structure can make a per-share
#: figure large after a reverse split. Neither is bounded from below because a
#: loss-making quarter legitimately reports negative EPS.
#: No magnitude or sign bound is applied to reported figures, because the
#: database applies none either. A company can post a loss, so ``reported_eps``
#: is legitimately negative, and a restatement can move any figure. The columns
#: are ``NUMERIC(20, 4)`` for EPS and ``NUMERIC(24, 4)`` for revenue, and those
#: declarations are the only limit: anything wider fails at flush against the
#: real column type rather than against a ceiling invented at the boundary.


class EarningsEventCreate(CodeValidatingSchema):
    """Payload for storing a reported earnings result.

    Reported and estimated figures are stored side by side and never combined
    here. EPS surprise, revenue surprise, post-event returns and abnormal returns
    are derived later in the analytics layer, where the methodology is versioned
    and re-runnable; persisting a surprise at write time would freeze one
    definition into the fact table.

    ``timing`` records the session phase the source declared and nothing more.
    It is not a tradability judgement and carries no assertion about which
    session the event is actionable in.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    company_id: UUID
    fiscal_year: int = Field(ge=MIN_FISCAL_YEAR, le=MAX_FISCAL_YEAR)
    fiscal_quarter: int | None = Field(default=None, ge=MIN_FISCAL_QUARTER, le=MAX_FISCAL_QUARTER)
    event_date: date
    event_timestamp: datetime | None = None
    timing: EarningsTiming = EarningsTiming.UNKNOWN
    reported_eps: Decimal | None = None
    estimated_eps: Decimal | None = None
    reported_revenue: Decimal | None = None
    estimated_revenue: Decimal | None = None
    currency: str | None = Field(default=None, max_length=CURRENCY_LENGTH)
    source_id: UUID
    observed_at: datetime | None = None
    observed_at_basis: AvailabilityBasis | None = None
    ingested_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("event_timestamp")
    @classmethod
    def _check_event_timestamp(cls, value: datetime | None) -> datetime | None:
        """Require a timezone-aware event timestamp, when one is supplied, and
        normalise it to UTC.

        ``None`` means the source gave no exact instant and is passed through
        unchanged -- never defaulted to midnight or any other guess. When a
        value *is* supplied, a naive one that PostgreSQL would silently
        reinterpret in the session time zone is rejected outright rather than
        converted on a guess.
        """
        return None if value is None else as_utc_aware(value)

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
    def _check_observed_at_pairing(self) -> EarningsEventCreate:
        """``observed_at`` and what it proves are supplied together or not at all."""
        if (self.observed_at is None) != (self.observed_at_basis is None):
            message = "observed_at and observed_at_basis must be set together or both omitted."
            raise ValueError(message)
        return self


class EarningsEventRead(OrmReadSchema):
    """Read model for a reported earnings result.

    There is deliberately no ``eps_surprise``, ``surprise_pct`` or
    ``return_1d`` field: those are analytics, not stored facts.
    """

    id: UUID
    company_id: UUID
    fiscal_year: int
    fiscal_quarter: int | None
    event_date: date
    event_timestamp: datetime | None
    timing: EarningsTiming
    reported_eps: Decimal | None
    estimated_eps: Decimal | None
    reported_revenue: Decimal | None
    estimated_revenue: Decimal | None
    currency: str | None
    source_id: UUID
    observed_at: datetime | None
    observed_at_basis: AvailabilityBasis | None
    ingested_at: datetime
    created_at: datetime
    updated_at: datetime
