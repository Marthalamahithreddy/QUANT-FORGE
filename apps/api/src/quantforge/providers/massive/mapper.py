"""Massive payload -> QuantForge canonical values.

The only place Massive's field names are interpreted. Everything downstream --
the reference resolver, the daily-bar writer, the models, the tests for both --
speaks in the vocabulary of :mod:`quantforge.ingestion.canonical`.

The mapping decisions that are not mechanical
---------------------------------------------

*CIK zero-padding and FIGI blank handling* live in the DTO's validators, not
here, so a malformed CIK is rejected while the provider layer is still the thing
that knows what a CIK is. This module composes what the DTO already normalised.

*The venue is resolved, not passed through.* ``primary_exchange`` is a vendor
string; ``Exchange.mic`` is an ISO 10383 code. The translation goes through
:mod:`quantforge.providers.massive.exchanges`, which is an explicit table with
no defaults. A venue that is not in the table is a hard error, because
defaulting it would place a listing on the wrong timezone and silently shift its
session dates.

*Session dates come from the venue timezone, not UTC.* A daily aggregate's
timestamp is an instant. Which calendar day it belongs to depends on where it
traded, and for an instant between 00:00 and 04:00 UTC that is the *previous*
day in New York. Deriving the date from the instant's own UTC date would
mis-file every early-morning print.

*Security type codes are mapped explicitly.* ``CS`` and ``PS`` are the only codes
this milestone understands. An unrecognised code is a failure rather than a
default, because the alternative -- filing an ETF or a warrant into the equity
tables -- corrupts the dataset in a way that is not obvious later.

The whole mapping is pure: given the same decoded document it returns the same
canonical values, and it performs no I/O. That is what makes the ingestion
services testable without a provider.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from quantforge.ingestion.canonical import (
    CanonicalDailyBar,
    CanonicalEquityReference,
    ResolvedVenue,
)
from quantforge.providers.base import VenueView
from quantforge.providers.errors import ProviderPayloadError
from quantforge.providers.massive.dto import (
    MASSIVE_SECURITY_TYPE_BY_CODE,
    MassiveDailyBarDTO,
    MassiveTickerDetailsDTO,
)
from quantforge.providers.massive.exchanges import (
    UnsupportedExchangeError,
    resolve_venue,
)

__all__ = [
    "MASSIVE_PROVIDER_NAME",
    "to_canonical_daily_bar",
    "to_canonical_reference",
]

#: Stable provider identity. Also the ``DataSource.name`` and
#: ``IngestionRun.provider`` value; changing it would orphan existing rows.
MASSIVE_PROVIDER_NAME: Final[str] = "massive"

#: Timezone assumed when a venue resolves but its IANA zone is somehow missing
#: from the system tzdata. Only reachable on a broken image; a wrong session date
#: is better than a crash mid-ingest, and this is US-equity-only.
_FALLBACK_VENUE_TZ: Final[str] = "America/New_York"


def _venue_timezone(venue: VenueView) -> ZoneInfo:
    """Load the venue's IANA zone, falling back rather than failing the bar."""
    try:
        return ZoneInfo(venue.timezone_name)
    except (ZoneInfoNotFoundError, ValueError):  # fmt: skip
        return ZoneInfo(_FALLBACK_VENUE_TZ)


def to_canonical_reference(
    dto: MassiveTickerDetailsDTO,
    *,
    identifier: str | None = None,
) -> CanonicalEquityReference:
    """Map one ticker-details DTO to a canonical reference.

    Raises :class:`ProviderPayloadError` when the payload is not a US equity we
    can store: a non-US locale, an unmapped venue, or an instrument class code
    this milestone does not claim to understand. Each of those is a deliberate
    refusal rather than a lossy conversion.
    """
    label = identifier or dto.ticker

    if not dto.is_us_listed:
        message = (
            f"{label}: locale {dto.locale!r} is not 'us'; this pipeline is "
            "US-equity-only and will not guess at another market's conventions"
        )
        raise ProviderPayloadError(message, identifier=label)

    if dto.security_type_code is None:
        message = f"{label}: provider supplied no instrument class ('type')"
        raise ProviderPayloadError(message, identifier=label)

    security_type = MASSIVE_SECURITY_TYPE_BY_CODE.get(dto.security_type_code)
    if security_type is None:
        message = (
            f"{label}: instrument class {dto.security_type_code!r} is not "
            f"recognised; understood codes are "
            f"{sorted(MASSIVE_SECURITY_TYPE_BY_CODE)}. Filing it as equity would "
            "put a non-equity instrument into the equity tables"
        )
        raise ProviderPayloadError(message, identifier=label)

    # An unmapped venue raises UnsupportedExchangeError from the exchange module.
    # That is intended to escape: it is a configuration gap, not bad data, and the
    # operator needs to see it as such.
    try:
        identity = resolve_venue(dto.provider_exchange_code)
    except UnsupportedExchangeError as exc:
        # Re-raised with the offending symbol attached. The exchange module does
        # not know which ticker was being resolved, and a run ingesting 500
        # symbols needs to know which row failed.
        raise UnsupportedExchangeError(f"{label}: {exc}") from exc
    venue = ResolvedVenue(
        mic=identity.mic,
        name=identity.name,
        timezone_name=identity.timezone,
    )

    try:
        return CanonicalEquityReference(
            ticker=dto.ticker,
            security_type=security_type,
            venue=venue,
            company_name=dto.name,
            cik=dto.cik,
            composite_figi=dto.composite_figi,
            share_class_figi=dto.share_class_figi,
            currency_code=dto.currency_code,
            is_active=dto.is_active,
        )
    except ValueError as exc:
        # e.g. a CIK the DTO validator let through but the canonical invariant
        # rejects. Re-raised as a payload error so callers have one thing to catch.
        raise ProviderPayloadError(f"{label}: {exc}", identifier=label) from exc


def to_canonical_daily_bar(
    dto: MassiveDailyBarDTO,
    *,
    venue: VenueView,
    identifier: str | None = None,
) -> CanonicalDailyBar:
    """Map one aggregate DTO to a canonical daily bar for a known venue.

    The venue is passed in rather than looked up: the bar endpoint returns no
    exchange code, so the listing already resolved during reference ingestion is
    the authority on which timezone applies. Guessing it here would let the
    reference and the bars disagree about what date a bar belongs to.

    ``observed_at`` is deliberately ``None``. A daily aggregate carries no
    availability timestamp, and inferring one from the session close would assert
    a point-in-time knowledge that was never true.
    """
    label = identifier or f"{venue.mic}@{dto.timestamp_ms}"

    try:
        # ``session_instant`` itself does epoch arithmetic on ``timestamp_ms``
        # and can overflow for an absurd value, same as ``astimezone`` below --
        # both belong to the one "this timestamp is out of range" refusal.
        instant: datetime = dto.session_instant
        session_date = instant.astimezone(_venue_timezone(venue)).date()
    except (ValueError, OverflowError) as exc:
        message = f"{label}: timestamp {dto.timestamp_ms} is out of range: {exc}"
        raise ProviderPayloadError(message, identifier=identifier) from exc

    try:
        return CanonicalDailyBar(
            trading_session_date=session_date,
            open=_as_decimal(dto.open, "open", label),
            high=_as_decimal(dto.high, "high", label),
            low=_as_decimal(dto.low, "low", label),
            close=_as_decimal(dto.close, "close", label),
            volume=dto.volume,
            observed_at=None,
            vwap=_as_optional_decimal(dto.vwap, "vwap", label),
            trades=dto.trades,
        )
    except ValueError as exc:
        raise ProviderPayloadError(
            f"{label}: {session_date.isoformat()}: {exc}", identifier=identifier
        ) from exc


def _as_decimal(value: Decimal, field: str, label: str) -> Decimal:
    """Re-assert finiteness on a value that is already a ``Decimal``.

    The canonical dataclass also checks this. Doing it here as well is
    deliberate: a non-finite decimal reaches ``dto.open`` already parsed, and
    ``Decimal("NaN") * 2`` being ``NaN`` means an ordering comparison against it
    can raise rather than return false. Catching it here reports the offending
    record by name instead of failing somewhere unrelated.
    """
    if not value.is_finite():
        message = f"{label}: {field} is {value}, which is not a finite price"
        raise ValueError(message)
    return value


def _as_optional_decimal(value: Decimal | None, field: str, label: str) -> Decimal | None:
    if value is None:
        return None
    return _as_decimal(value, field, label)


def session_date_for(venue: VenueView, instant: datetime) -> date:
    """The calendar date ``instant`` belongs to in ``venue``'s local time.

    Exposed for tests and for callers that need the same conversion without
    building a bar. The single definition of "which day was this", so the
    reference path and the bar path cannot disagree.
    """
    return instant.astimezone(_venue_timezone(venue)).date()
