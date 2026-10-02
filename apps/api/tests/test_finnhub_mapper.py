"""Unit tests for the Finnhub DTO and DTO -> canonical mapper.

Pure and synchronous throughout: no HTTP, no database. The one property that
matters most here is negative: this mapper must never produce an
``event_timestamp``, because Finnhub never supplies one -- see
``quantforge.providers.finnhub.mapper``'s module docstring.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from quantforge.models.enums import EarningsTiming
from quantforge.providers.errors import ProviderPayloadError
from quantforge.providers.finnhub.dto import (
    FinnhubEarningsCalendarEntry,
    FinnhubEarningsCalendarResponse,
)
from quantforge.providers.finnhub.mapper import to_canonical_earnings_event
from quantforge.providers.json_decimal import decode_json_document


def _entry(**overrides: object) -> FinnhubEarningsCalendarEntry:
    fields: dict[str, object] = {
        "symbol": "AAPL",
        "date": "2026-05-01",
        "quarter": 2,
        "year": 2026,
        "epsActual": "1.52",
        "epsEstimate": "1.43",
        "revenueActual": "123456789",
        "revenueEstimate": "120000000",
        "hour": "amc",
    }
    fields.update(overrides)
    return FinnhubEarningsCalendarEntry.model_validate(fields)


def _decoded_entry(**overrides: object) -> FinnhubEarningsCalendarEntry:
    """Round-trip through the real Decimal-safe JSON decoder, the way a live
    response actually would, rather than handing pydantic pre-built Decimals.
    """
    fields: dict[str, object] = {
        "symbol": "AAPL",
        "date": "2026-05-01",
        "quarter": 2,
        "year": 2026,
        "epsActual": -0.53,
        "epsEstimate": -0.8571163,
        "revenueActual": 1004700000,
        "revenueEstimate": 1105412931,
        "hour": "amc",
    }
    fields.update(overrides)
    decoded = decode_json_document(json.dumps(fields).encode())
    assert isinstance(decoded, dict)
    return FinnhubEarningsCalendarEntry.model_validate(decoded)


# ── DTO ──────────────────────────────────────────────────────────────────────


def test_a_complete_entry_parses_every_field() -> None:
    entry = _entry()
    assert entry.symbol == "AAPL"
    assert entry.date == date(2026, 5, 1)
    assert entry.quarter == 2
    assert entry.year == 2026
    assert entry.eps_actual == Decimal("1.52")
    assert entry.hour == "amc"


def test_a_blank_symbol_is_refused() -> None:
    with pytest.raises(ValidationError, match="blank symbol"):
        _entry(symbol="   ")


def test_symbol_is_normalised_to_upper_case() -> None:
    assert _entry(symbol="aapl").symbol == "AAPL"


def test_missing_estimates_are_none_not_zero() -> None:
    entry = _entry(epsEstimate=None, revenueEstimate=None)
    assert entry.eps_estimate is None
    assert entry.revenue_estimate is None


def test_missing_revenue_is_none() -> None:
    entry = _entry(revenueActual=None, revenueEstimate=None)
    assert entry.revenue_actual is None
    assert entry.revenue_estimate is None


def test_quarter_is_optional_for_annual_filers() -> None:
    assert _entry(quarter=None).quarter is None


def test_an_additive_unknown_field_is_tolerated() -> None:
    """A future Finnhub field must not break parsing of the fields this
    adapter actually uses."""
    entry = _entry(estimateCount=7)
    assert entry.symbol == "AAPL"


def test_a_malformed_date_fails_clearly() -> None:
    with pytest.raises(ValidationError):
        _entry(date="not-a-date")


def test_negative_eps_survives_as_an_exact_decimal_through_real_json_decoding() -> None:
    """Round-tripped through the real Decimal-safe decoder, exactly as a live
    response would be -- not hand-built pydantic input."""
    entry = _decoded_entry()
    assert entry.eps_actual == Decimal("-0.53")
    assert isinstance(entry.eps_actual, Decimal)
    assert entry.eps_estimate == Decimal("-0.8571163")


def test_whole_dollar_revenue_from_json_survives_as_an_exact_decimal() -> None:
    """Finnhub's example payloads spell revenue with no decimal point at all
    (``1004700000``), which the JSON decoder reads as a plain ``int`` -- this
    must still arrive as an exact ``Decimal``, never a float round-trip."""
    entry = _decoded_entry()
    assert entry.revenue_actual == Decimal("1004700000")
    assert isinstance(entry.revenue_actual, Decimal)


def test_the_envelope_parses_the_named_array() -> None:
    envelope = FinnhubEarningsCalendarResponse.model_validate(
        {"earningsCalendar": [_entry().model_dump(by_alias=True)]}
    )
    assert len(envelope.earnings_calendar) == 1


def test_an_empty_envelope_is_a_legitimate_empty_result() -> None:
    envelope = FinnhubEarningsCalendarResponse.model_validate({"earningsCalendar": []})
    assert envelope.earnings_calendar == []


def test_a_missing_array_key_defaults_to_empty_rather_than_erroring() -> None:
    envelope = FinnhubEarningsCalendarResponse.model_validate({})
    assert envelope.earnings_calendar == []


# ── mapper: the core contract ────────────────────────────────────────────────


def test_event_timestamp_is_always_none() -> None:
    """The one property this mapper must never violate: Finnhub never states
    an exact instant, so the canonical event must never have one either."""
    event = to_canonical_earnings_event(_entry())
    assert event.event_timestamp is None


def test_event_date_comes_straight_from_the_provider_date() -> None:
    event = to_canonical_earnings_event(_entry(date="2026-08-15"))
    assert event.event_date == date(2026, 8, 15)


def test_observed_at_is_always_none() -> None:
    """Finnhub states no distinct "observation available" timestamp."""
    event = to_canonical_earnings_event(_entry())
    assert event.observed_at is None


def test_currency_is_none_not_an_assumed_usd() -> None:
    """Not documented by this endpoint, so it must not be invented."""
    event = to_canonical_earnings_event(_entry())
    assert event.currency is None


def test_fiscal_year_and_quarter_come_from_provider_fields_not_the_date() -> None:
    event = to_canonical_earnings_event(_entry(year=2025, quarter=4, date="2026-01-03"))
    assert event.fiscal_year == 2025
    assert event.fiscal_quarter == 4


def test_negative_eps_is_preserved_through_the_mapper() -> None:
    event = to_canonical_earnings_event(_decoded_entry())
    assert event.reported_eps == Decimal("-0.53")


def test_actual_and_estimated_values_both_map() -> None:
    event = to_canonical_earnings_event(_entry())
    assert event.reported_eps == Decimal("1.52")
    assert event.estimated_eps == Decimal("1.43")
    assert event.reported_revenue == Decimal("123456789")
    assert event.estimated_revenue == Decimal("120000000")


def test_missing_estimates_map_to_none() -> None:
    event = to_canonical_earnings_event(_entry(epsEstimate=None, revenueEstimate=None))
    assert event.estimated_eps is None
    assert event.estimated_revenue is None


# ── mapper: timing ───────────────────────────────────────────────────────────


def test_bmo_maps_to_before_market() -> None:
    assert to_canonical_earnings_event(_entry(hour="bmo")).timing is EarningsTiming.BEFORE_MARKET


def test_amc_maps_to_after_market() -> None:
    assert to_canonical_earnings_event(_entry(hour="amc")).timing is EarningsTiming.AFTER_MARKET


def test_dmh_maps_to_during_market() -> None:
    assert to_canonical_earnings_event(_entry(hour="dmh")).timing is EarningsTiming.DURING_MARKET


def test_timing_matching_is_case_insensitive() -> None:
    assert to_canonical_earnings_event(_entry(hour="AMC")).timing is EarningsTiming.AFTER_MARKET


def test_a_missing_hour_maps_to_unknown() -> None:
    assert to_canonical_earnings_event(_entry(hour=None)).timing is EarningsTiming.UNKNOWN


def test_an_unrecognised_hour_maps_to_unknown_not_an_error() -> None:
    """A future Finnhub value, or a typo, must degrade to UNKNOWN rather than
    fail the whole record -- the value is not lost, it survives in the raw
    response bytes the raw store persists verbatim."""
    assert to_canonical_earnings_event(_entry(hour="lunch")).timing is EarningsTiming.UNKNOWN


# ── mapper: malformed records ────────────────────────────────────────────────


def test_a_canonical_construction_failure_is_wrapped_as_a_payload_error() -> None:
    """An out-of-range fiscal quarter is not bound-checked by the DTO itself
    (deferred to the canonical layer, matching the Massive adapter's own
    division of labour), so it reaches ``CanonicalEarningsEvent``'s own range
    check -- and must surface as one kind of error the caller already knows
    to catch, not a raw ``ValueError`` from a different layer.
    """
    entry = _entry(quarter=9)
    with pytest.raises(ProviderPayloadError, match="AAPL"):
        to_canonical_earnings_event(entry, identifier="AAPL")
