"""Finnhub's own response shapes, decoded but not yet canonical.

Only ``GET /calendar/earnings`` is modelled here -- the only endpoint this
adapter uses. Fields are declared for exactly what Finnhub documents; nothing
is invented, and an additive field Finnhub might add later is tolerated
(``extra="ignore"``) rather than rejected.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = ["FinnhubEarningsCalendarEntry", "FinnhubEarningsCalendarResponse"]


class _FinnhubModel(BaseModel):
    """Shared configuration for Finnhub DTOs.

    Mirrors the Massive adapter's own DTO base: tolerate an additive field,
    never coerce a wrong type silently.
    """

    model_config = ConfigDict(extra="ignore", frozen=True, strict=False, populate_by_name=True)


class FinnhubEarningsCalendarEntry(_FinnhubModel):
    """One entry from ``/calendar/earnings``'s ``earningsCalendar`` array.

    Every figure is nullable individually because Finnhub routinely reports
    one without the other -- an estimate before the actual is known, or an
    actual with no consensus estimate on record. ``None`` here always means
    "Finnhub did not supply this", never zero.

    Deliberately absent: any exact-instant field. Finnhub's earnings
    calendar states a calendar date and a coarse session tag
    (:attr:`hour`) only -- see the module docstring on
    :mod:`quantforge.providers.finnhub.mapper` for what that means for
    ``CanonicalEarningsEvent.event_timestamp``.
    """

    symbol: str
    date: date
    quarter: int | None = None
    year: int
    eps_actual: Decimal | None = Field(default=None, alias="epsActual")
    eps_estimate: Decimal | None = Field(default=None, alias="epsEstimate")
    revenue_actual: Decimal | None = Field(default=None, alias="revenueActual")
    revenue_estimate: Decimal | None = Field(default=None, alias="revenueEstimate")
    #: Session-timing tag, e.g. ``"bmo"``/``"amc"``/``"dmh"``. Passed through
    #: as Finnhub spelled it; the mapper decides what it means and preserves
    #: the raw value regardless, since the raw response is stored verbatim.
    hour: str | None = None

    @field_validator("symbol")
    @classmethod
    def _require_non_blank_symbol(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            message = "provider returned a blank symbol"
            raise ValueError(message)
        return stripped.upper()


class FinnhubEarningsCalendarResponse(_FinnhubModel):
    """The envelope ``/calendar/earnings`` returns: one named array."""

    earnings_calendar: list[FinnhubEarningsCalendarEntry] = Field(
        default_factory=list, alias="earningsCalendar"
    )
