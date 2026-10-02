"""Finnhub DTO -> canonical earnings event.

The one mapping decision that is not mechanical
-------------------------------------------------
Finnhub's earnings calendar states a calendar date (:attr:`~.dto.FinnhubEarningsCalendarEntry.date`)
and a coarse session tag (:attr:`~.dto.FinnhubEarningsCalendarEntry.hour`) --
never an exact instant. ``event_timestamp`` is therefore **always** ``None``
for every event this mapper produces. This is not a gap to work around: it is
the honest shape of what the source actually states, and
``CanonicalEarningsEvent`` exists to carry a date-only observation exactly as
faithfully as an exact-instant one. Manufacturing midnight, 09:30 or 16:00 out
of the date and the session tag would assert a precision Finnhub never gave.

``timing`` is mapped only from documented values (``bmo``/``amc``/``dmh``);
anything else -- a missing tag, an unrecognised one, or a future addition to
Finnhub's vocabulary -- becomes :attr:`EarningsTiming.UNKNOWN` rather than a
guess. The raw string survives regardless, in the untouched response bytes the
raw store persists, so a future, better-evidenced mapping can be added without
having lost anything.

``observed_at`` is left ``None``: Finnhub's calendar endpoint states no
timestamp for when an estimate or actual became available, as distinct from
the event's own date, so there is nothing genuine to put there.
"""

from __future__ import annotations

from quantforge.ingestion.canonical import CanonicalEarningsEvent
from quantforge.models.enums import EarningsTiming
from quantforge.providers.errors import ProviderPayloadError
from quantforge.providers.finnhub.dto import FinnhubEarningsCalendarEntry

__all__ = ["to_canonical_earnings_event"]

#: Only these are documented Finnhub session-timing values. Deliberately a
#: closed, explicit table rather than a heuristic: an unrecognised or missing
#: tag must fall through to UNKNOWN, never be guessed at.
_TIMING_BY_HOUR: dict[str, EarningsTiming] = {
    "bmo": EarningsTiming.BEFORE_MARKET,
    "amc": EarningsTiming.AFTER_MARKET,
    "dmh": EarningsTiming.DURING_MARKET,
}


def _map_timing(hour: str | None) -> EarningsTiming:
    if hour is None:
        return EarningsTiming.UNKNOWN
    return _TIMING_BY_HOUR.get(hour.strip().lower(), EarningsTiming.UNKNOWN)


def to_canonical_earnings_event(
    entry: FinnhubEarningsCalendarEntry,
    *,
    identifier: str | None = None,
) -> CanonicalEarningsEvent:
    """Map one earnings-calendar entry to a canonical event.

    Raises :class:`ProviderPayloadError` when the entry cannot satisfy the
    canonical type's own invariants (e.g. a fiscal quarter outside 1-4) --
    wrapped so callers have one kind of error to catch, exactly as the
    Massive mapper does for reference records.
    """
    label = identifier or entry.symbol
    try:
        return CanonicalEarningsEvent(
            ticker=entry.symbol,
            fiscal_year=entry.year,
            fiscal_quarter=entry.quarter,
            event_date=entry.date,
            timing=_map_timing(entry.hour),
            # Never an exact instant -- see the module docstring.
            event_timestamp=None,
            reported_eps=entry.eps_actual,
            estimated_eps=entry.eps_estimate,
            reported_revenue=entry.revenue_actual,
            estimated_revenue=entry.revenue_estimate,
            # Not documented by this endpoint; None means "Finnhub did not
            # state one", not an assumed USD.
            currency=None,
            # Never manufactured; see the module docstring.
            observed_at=None,
        )
    except ValueError as exc:
        raise ProviderPayloadError(f"{label}: {exc}", identifier=label) from exc
