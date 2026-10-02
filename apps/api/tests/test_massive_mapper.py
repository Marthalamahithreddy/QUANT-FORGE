"""Unit tests for the Massive DTO -> canonical mapper.

The mapper is the one place a malformed or unrecognised provider payload turns
into a refusal rather than a silently wrong row, so its error paths are worth
pinning directly rather than only exercising them incidentally through the
higher-level client tests. Pure and synchronous throughout: no HTTP, no
database, DTOs built directly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from quantforge.providers.errors import ProviderPayloadError
from quantforge.providers.massive.dto import MassiveDailyBarDTO, MassiveTickerDetailsDTO
from quantforge.providers.massive.exchanges import UnsupportedExchangeError
from quantforge.providers.massive.mapper import (
    _as_decimal,
    _venue_timezone,
    session_date_for,
    to_canonical_daily_bar,
    to_canonical_reference,
)


class _Venue:
    """Minimal stand-in for :class:`VenueView`."""

    def __init__(
        self, mic: str = "XNAS", name: str = "NasdaqGS", timezone_name: str = "America/New_York"
    ) -> None:
        self.mic = mic
        self.name = name
        self.timezone_name = timezone_name


def _ticker_dto(**overrides: object) -> MassiveTickerDetailsDTO:
    fields: dict[str, object] = {
        "ticker": "AAPL",
        "name": "Apple Inc.",
        "locale": "us",
        "provider_exchange_code": "XNAS",
        "security_type_code": "CS",
        "currency_code": "USD",
        "is_active": True,
        "cik": "0000320193",
        "composite_figi": "BBG000B9XRY4",
        "share_class_figi": "BBG001S5QY8",
    }
    fields.update(overrides)
    return MassiveTickerDetailsDTO(**fields)


def _bar_dto(**overrides: object) -> MassiveDailyBarDTO:
    fields: dict[str, object] = {
        "open": Decimal("199.50"),
        "high": Decimal("201.00"),
        "low": Decimal("198.75"),
        "close": Decimal("200.25"),
        "volume": 1_000_000,
        "timestamp_ms": 1_726_500_000_000,
    }
    fields.update(overrides)
    return MassiveDailyBarDTO(**fields)


# ── _venue_timezone ─────────────────────────────────────────────────────────


def test_venue_timezone_resolves_a_real_iana_zone() -> None:
    zone = _venue_timezone(_Venue(timezone_name="America/New_York"))
    assert str(zone) == "America/New_York"


def test_venue_timezone_falls_back_on_an_unknown_zone_name() -> None:
    """A stored venue whose IANA name the runtime cannot resolve must not crash
    ingestion -- a wrong session date is preferable to losing the bar entirely.
    """
    zone = _venue_timezone(_Venue(timezone_name="Not/A_Real_Zone"))
    assert str(zone) == "America/New_York"


def test_venue_timezone_falls_back_on_a_blank_zone_name() -> None:
    zone = _venue_timezone(_Venue(timezone_name=""))
    assert str(zone) == "America/New_York"


# ── to_canonical_reference: refusals ────────────────────────────────────────


def test_a_non_us_locale_is_refused() -> None:
    with pytest.raises(ProviderPayloadError, match="US-equity-only"):
        to_canonical_reference(_ticker_dto(locale="gb"))


def test_a_missing_security_type_code_is_refused() -> None:
    with pytest.raises(ProviderPayloadError, match="no instrument class"):
        to_canonical_reference(_ticker_dto(security_type_code=None))


def test_an_unrecognised_security_type_code_is_refused() -> None:
    with pytest.raises(ProviderPayloadError, match="not recognised"):
        to_canonical_reference(_ticker_dto(security_type_code="ETF"))


def test_an_unmapped_venue_is_refused_with_the_ticker_attached() -> None:
    """The exchange module does not know which ticker it was resolving; the
    mapper must attach it so a batch failure names the offending symbol.
    """
    with pytest.raises(UnsupportedExchangeError, match="AAPL"):
        to_canonical_reference(_ticker_dto(ticker="AAPL", provider_exchange_code="ZZZZ_UNKNOWN"))


def test_a_valid_reference_maps_every_field() -> None:
    result = to_canonical_reference(_ticker_dto(), identifier="AAPL")
    assert result.ticker == "AAPL"
    assert result.cik == "0000320193"
    assert result.venue is not None
    assert result.venue.mic == "XNAS"
    assert result.currency_code == "USD"


# ── to_canonical_daily_bar: refusals ────────────────────────────────────────


def test_an_out_of_range_timestamp_is_refused() -> None:
    """A timestamp so far outside representable range that ``astimezone``
    raises must be reported against the bar's own label, not crash the batch.
    """
    dto = _bar_dto(timestamp_ms=99_999_999_999_999_999)
    with pytest.raises(ProviderPayloadError, match="out of range"):
        to_canonical_daily_bar(dto, venue=_Venue())


def test_a_valid_bar_maps_every_field() -> None:
    result = to_canonical_daily_bar(_bar_dto(), venue=_Venue(), identifier="AAPL")
    assert result.open == Decimal("199.50")
    assert result.close == Decimal("200.25")
    assert result.volume == 1_000_000
    assert result.observed_at is None, "a daily aggregate carries no availability timestamp"


# ── _as_decimal / session_date_for ──────────────────────────────────────────


def test_as_decimal_rejects_infinity() -> None:
    with pytest.raises(ValueError, match="not a finite price"):
        _as_decimal(Decimal("Infinity"), "open", "AAPL")


def test_session_date_for_uses_the_venues_local_calendar_day() -> None:
    """An instant just after midnight UTC is still the *previous* evening in
    New York -- the exact case a UTC-derived date would mis-file.
    """
    instant = datetime(2026, 9, 15, 2, 0, tzinfo=UTC)
    session_date = session_date_for(_Venue(timezone_name="America/New_York"), instant)
    assert session_date.isoformat() == "2026-09-14"
