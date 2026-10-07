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
    CanonicalStockSplit,
    CanonicalTickerChange,
    CanonicalTickerSnapshot,
    ResolvedVenue,
)
from quantforge.models.enums import PriceBasis, StockSplitAdjustmentType
from quantforge.providers.base import VenueView
from quantforge.providers.errors import ProviderPayloadError
from quantforge.providers.massive.dto import (
    MASSIVE_SECURITY_TYPE_BY_CODE,
    MassiveDailyBarDTO,
    MassiveSplitDTO,
    MassiveTickerChangeEventDTO,
    MassiveTickerDetailsDTO,
    MassiveTickerSnapshotDTO,
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
    price_basis: PriceBasis,
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

    ``price_basis`` is the basis the caller *requested* and the client has
    already confirmed against the response's own ``adjusted`` field; it is
    stamped on the bar here and never inferred from the payload.
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
            price_basis=price_basis,
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


#: Massive's ``adjustment_type`` vocabulary -> QuantForge's. Explicit, no default:
#: a value Massive adds later must be reviewed, not mapped onto the nearest member.
_SPLIT_ADJUSTMENT_TYPES: Final[dict[str, StockSplitAdjustmentType]] = {
    "forward_split": StockSplitAdjustmentType.FORWARD_SPLIT,
    "reverse_split": StockSplitAdjustmentType.REVERSE_SPLIT,
    "stock_dividend": StockSplitAdjustmentType.STOCK_DIVIDEND,
}


def to_canonical_stock_split(dto: MassiveSplitDTO) -> CanonicalStockSplit:
    """Map one Massive split record, keeping its orientation explicit.

    ``split_from`` (old shares) becomes ``old_shares`` and ``split_to`` (new
    shares) becomes ``new_shares``; the ratio is never inverted or simplified.
    ``execution_date`` stays the calendar date it is -- it is not shifted to a
    session and not promoted to an availability time.

    Raises:
        ProviderPayloadError: the adjustment type is not one QuantForge knows, or
            the values fail the canonical invariants (a non-positive ratio, say).
    """
    adjustment = _SPLIT_ADJUSTMENT_TYPES.get(dto.adjustment_type.lower())
    if adjustment is None:
        message = f"unrecognised split adjustment_type {dto.adjustment_type!r}"
        raise ProviderPayloadError(message, identifier=dto.provider_split_id)
    try:
        return CanonicalStockSplit(
            provider_split_id=dto.provider_split_id,
            ticker=dto.ticker.upper(),
            execution_date=dto.execution_date,
            old_shares=dto.old_shares,
            new_shares=dto.new_shares,
            adjustment_type=adjustment,
        )
    except ValueError as exc:
        raise ProviderPayloadError(str(exc), identifier=dto.provider_split_id) from exc


#: Instrument types a ticker snapshot is turned into a listing-history fact for. The same two the
#: reference adapter understands; anything else (ETFs, warrants, ADRs...) is not modelled here.
_SNAPSHOT_SUPPORTED_TYPES: Final[frozenset[str]] = frozenset(MASSIVE_SECURITY_TYPE_BY_CODE)


def is_supported_snapshot_type(dto: MassiveTickerSnapshotDTO) -> bool:
    """Whether this snapshot record is a security type QuantForge models."""
    return dto.type is not None and dto.type.strip().upper() in _SNAPSHOT_SUPPORTED_TYPES


#: Massive's dated-snapshot contract (``GET /v3/reference/tickers``, read 2026-10-07): ``date`` --
#: "Specify a point in time to retrieve tickers available on that date"; ``active`` -- whether the
#: tickers returned are "actively traded on the queried date". So the REQUEST parameter is the
#: as-of date of the ticker's availability, and the adapter dates each record by it. The response
#: itself states no as-of date (``last_updated_utc`` is not one), and the returned attributes --
#: name, CIK, FIGIs, ``primary_exchange`` -- are NOT documented as of that date: they identify the
#: returned instrument, they are not dated evidence. That is why a snapshot's venue is never stored
#: as a fact. See docs/production-validation-design.md section 20 and
#: docs/market-data-repair-design.md section 6. Pinned by tests; no runtime code branches on these.
SNAPSHOT_DATE_SEMANTICS: Final[str] = "REQUEST_PARAMETER_IS_AS_OF_DATE"
SNAPSHOT_DATE_STATED_IN_RESPONSE: Final[bool] = False
SNAPSHOT_ATTRIBUTES_ARE_AS_OF_DATE: Final[bool] = False


def to_canonical_ticker_snapshot(
    dto: MassiveTickerSnapshotDTO, *, as_of_date: date
) -> CanonicalTickerSnapshot:
    """Map one dated-snapshot record. Dates are taken as stated; no interval is derived.

    ``as_of_date`` is the REQUEST's ``date`` parameter (see :data:`SNAPSHOT_DATE_SEMANTICS`); any
    date-like key in the payload is ignored. The provider's venue code becomes a MIC only through
    the explicit venue table; a code the table does not know leaves ``primary_mic`` empty (nothing
    is guessed). ``primary_mic`` is the provider's primary exchange as returned, NOT documented as
    of ``as_of_date`` (:data:`SNAPSHOT_ATTRIBUTES_ARE_AS_OF_DATE`). ``delisted_utc`` contributes
    only its calendar date.

    Raises:
        ProviderPayloadError: the values fail the canonical invariants.
    """
    mic: str | None = None
    if dto.primary_exchange is not None and dto.primary_exchange.strip():
        try:
            mic = resolve_venue(dto.primary_exchange).mic
        except UnsupportedExchangeError:
            mic = None
    delisted: date | None = None
    if dto.delisted_utc is not None and dto.delisted_utc.strip():
        try:
            delisted = date.fromisoformat(dto.delisted_utc.strip()[:10])
        except ValueError as exc:
            message = f"delisted_utc {dto.delisted_utc!r} has no ISO date"
            raise ProviderPayloadError(message, identifier=dto.ticker) from exc
    try:
        return CanonicalTickerSnapshot(
            as_of_date=as_of_date,
            ticker=dto.ticker,
            active=dto.active,
            share_class_figi=dto.share_class_figi,
            composite_figi=dto.composite_figi,
            cik=dto.cik,
            primary_mic=mic,
            delisted_date=delisted,
        )
    except ValueError as exc:
        raise ProviderPayloadError(str(exc), identifier=dto.ticker) from exc


#: Massive documents ``ticker_change`` events (experimental) but not what the event ``date`` means.
#: The adapter treats it as the INCLUSIVE first date of the new ticker. That is a provider-contract
#: assumption, isolated here: it is NOT independently established, no live call has checked it, and
#: nothing downstream may treat it as more than a provider-stated start. If a live probe shows a
#: different meaning, change this adapter's interpretation, not the identity architecture.
TICKER_EVENT_DATE_SEMANTICS_VERIFIED: Final[bool] = False


def to_canonical_ticker_change(dto: MassiveTickerChangeEventDTO) -> CanonicalTickerChange:
    """Map one ticker-change event: ``new_ticker`` became the symbol on ``date`` (inclusive)."""
    try:
        return CanonicalTickerChange(
            ticker=dto.new_ticker, effective_date=date.fromisoformat(dto.date)
        )
    except ValueError as exc:
        raise ProviderPayloadError(str(exc), identifier=dto.new_ticker) from exc
