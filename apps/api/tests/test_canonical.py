"""Tests for the canonical DTOs.

These are the boundary the provider layer defends: everything above speaks this
vocabulary, everything below must be converted into it. So the tests are mostly
about *refusal* -- a canonical object that exists is one already known to be
internally consistent, and each invariant below corresponds to a wrong value that
would otherwise survive all the way into a backtest.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from quantforge.ingestion.canonical import (
    CanonicalDailyBar,
    CanonicalEarningsEvent,
    CanonicalEquityReference,
    ResolvedVenue,
)
from quantforge.models.enums import EarningsTiming

XNAS = ResolvedVenue(mic="XNAS", name="NasdaqGS", timezone_name="America/New_York")


# ── venue ───────────────────────────────────────────────────────────────────


def test_venue_accepts_a_real_timezone() -> None:
    assert XNAS.timezone_name == "America/New_York"


def test_venue_rejects_an_unknown_timezone() -> None:
    """A bad zone name must fail when the mapping is written, not at query time.

    The zone decides which calendar day a bar belongs to. A typo in an exchange
    table would otherwise misdate every bar for that venue by a day, silently and
    plausibly.
    """
    with pytest.raises(ValueError, match="IANA"):
        ResolvedVenue(mic="XNAS", name="NasdaqGS", timezone_name="Mars/Olympus_Mons")


# ── bars ────────────────────────────────────────────────────────────────────


def _bar(**overrides: object) -> CanonicalDailyBar:
    fields: dict[str, object] = {
        "trading_session_date": date(2026, 9, 1),
        "open": Decimal("100.00"),
        "high": Decimal("105.00"),
        "low": Decimal("99.00"),
        "close": Decimal("104.00"),
        "volume": 1_000_000,
    }
    fields.update(overrides)
    return CanonicalDailyBar(**fields)  # type: ignore[arg-type]


def test_a_consistent_bar_is_accepted() -> None:
    bar = _bar()
    assert bar.low <= bar.open <= bar.high
    assert bar.low <= bar.close <= bar.high


@pytest.mark.parametrize(
    ("field", "value", "why"),
    [
        ("low", Decimal("106"), "low above high"),
        ("high", Decimal("98"), "high below low"),
        ("open", Decimal("106"), "open above the session high"),
        ("open", Decimal("98"), "open below the session low"),
        ("close", Decimal("106"), "close above the session high"),
        ("close", Decimal("98"), "close below the session low"),
    ],
)
def test_an_inconsistent_bar_is_refused(field: str, value: Decimal, why: str) -> None:
    """A bar violating its own range is corrupt, not unusual.

    Storing it yields a chart that cannot be drawn and a return computed from a
    high below the low. The alternative -- "correcting" it by swapping values --
    is worse, because it hides that the data was bad.
    """
    with pytest.raises(ValueError):
        _bar(**{field: value})


@pytest.mark.parametrize("value", [Decimal("0"), Decimal("-1"), Decimal("-0.0001")])
def test_non_positive_prices_are_refused(value: Decimal) -> None:
    """No venue prints a zero or negative equity price; a zero would divide by
    zero in every return calculation that touches the row."""
    with pytest.raises(ValueError):
        _bar(close=value)


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_non_finite_prices_are_refused(value: Decimal) -> None:
    """``NaN`` satisfies every comparison, so it would pass every other check.

    It then propagates silently: no error, no row, and a backtest that is quietly
    nonsense.
    """
    with pytest.raises(ValueError):
        _bar(close=value)


def test_an_absurd_price_is_refused_as_a_field_mismatch() -> None:
    """A market cap or index level in a price column is caught by the ceiling.

    Not a units check -- it cannot be -- but it does catch the gross cases.
    """
    with pytest.raises(ValueError, match="gross-corruption"):
        _bar(close=Decimal("3e12"))


def test_negative_volume_is_refused() -> None:
    with pytest.raises(ValueError):
        _bar(volume=-1)


def test_negative_trades_is_refused() -> None:
    with pytest.raises(ValueError):
        _bar(trades=-1)


def test_absent_volume_and_trades_are_allowed() -> None:
    """A provider that omits them gives a legitimate unknown, not a zero.

    Storing 0 would assert "no shares traded", which is a different and stronger
    claim than "not reported".
    """
    bar = _bar(volume=None, vwap=None, trades=None)
    assert bar.volume is None
    assert bar.trades is None


def test_observed_at_is_optional_and_defaults_to_none() -> None:
    """Availability time is unknown unless the provider states it.

    Defaulting it to "now" would fabricate a fact -- that the value was available
    at ingestion time -- which is precisely how a point-in-time backtest acquires
    look-ahead bias.
    """
    assert _bar().observed_at is None


def test_naive_observed_at_is_refused() -> None:
    """A naive timestamp is ambiguous: it means different instants on different
    machines, and a point-in-time query would silently return the wrong rows."""
    with pytest.raises(ValueError, match="timezone-aware"):
        _bar(observed_at=datetime(2026, 9, 1, 20, 0, 0))


def test_aware_observed_at_is_accepted() -> None:
    stamp = datetime(2026, 9, 1, 20, 0, tzinfo=timezone(timedelta(hours=-4)))
    assert _bar(observed_at=stamp).observed_at == stamp


def test_vwap_must_sit_inside_the_session_range_too() -> None:
    """A vwap outside the high/low range is impossible for a real session.

    Checked explicitly because it is the one price field a naive implementation
    forgets, and because a vwap is often carried through unvalidated.
    """
    with pytest.raises(ValueError):
        _bar(vwap=Decimal("500"))


# ── reference identity ──────────────────────────────────────────────────────


def _reference(**overrides: object) -> CanonicalEquityReference:
    fields: dict[str, object] = {
        "ticker": "AAPL",
        "security_type": "EQUITY",
        "venue": XNAS,
        "company_name": "Apple Inc.",
        "cik": "0000320193",
        "composite_figi": "BBG000B9XRY4",
        "share_class_figi": "BBG001S5QY8",
        "currency_code": "USD",
    }
    fields.update(overrides)
    return CanonicalEquityReference(**fields)  # type: ignore[arg-type]


def test_cik_is_the_preferred_identity() -> None:
    """CIK names an issuer; a ticker does not, because symbols get reused."""
    assert _reference().identity_key == "cik:0000320193"


def test_figi_is_the_fallback_identity() -> None:
    assert _reference(cik=None).identity_key == "figi:BBG001S5QY8"


def test_composite_figi_is_the_last_resort() -> None:
    ref = _reference(cik=None, share_class_figi=None)
    assert ref.identity_key == "cfigi:BBG000B9XRY4"


def test_no_stable_identity_is_an_explicit_failure() -> None:
    """Without a CIK or FIGI there is nothing to match on.

    The tempting fallback is name similarity, which merges distinct issuers whose
    names collide. This raises instead, so that decision stays visible.
    """
    ref = _reference(cik=None, share_class_figi=None, composite_figi=None)
    with pytest.raises(ValueError, match="stable identity"):
        _ = ref.identity_key


def test_a_malformed_cik_is_refused_at_construction() -> None:
    """A CIK that is not ten digits matches no real issuer.

    Storing it would create a row that silently never joins, which is much harder
    to notice than a rejected write.
    """
    for bad in ("320193", "000032019X", "00003201931", ""):
        with pytest.raises(ValueError):
            _reference(cik=bad)


def test_a_blank_ticker_or_type_is_refused() -> None:
    with pytest.raises(ValueError):
        _reference(ticker="   ")
    with pytest.raises(ValueError):
        _reference(security_type="")


def test_optional_fields_may_be_absent() -> None:
    """A sparse reference is legitimate; the identity key is the only requirement."""
    ref = CanonicalEquityReference(
        ticker="XYZ", security_type="EQUITY", venue=XNAS, cik="0000000001"
    )
    assert ref.company_name is None
    assert ref.currency_code is None
    assert ref.is_active is None
    assert ref.identity_key == "cik:0000000001"


# ── earnings ────────────────────────────────────────────────────────────────


def _event(**overrides: object) -> CanonicalEarningsEvent:
    fields: dict[str, object] = {
        "ticker": "AAPL",
        "fiscal_year": 2026,
        "fiscal_quarter": 1,
        "event_date": date(2026, 5, 1),
        "event_timestamp": datetime(2026, 5, 1, 20, 30, tzinfo=UTC),
        "timing": EarningsTiming.AFTER_MARKET,
        "reported_eps": Decimal("1.52"),
        "estimated_eps": Decimal("1.43"),
        "reported_revenue": Decimal("123456789.00"),
        "estimated_revenue": Decimal("120000000.00"),
        "currency": "USD",
    }
    fields.update(overrides)
    return CanonicalEarningsEvent(**fields)  # type: ignore[arg-type]


def test_a_consistent_earnings_event_is_accepted() -> None:
    event = _event()
    assert event.ticker == "AAPL"
    assert event.fiscal_year == 2026
    assert event.fiscal_quarter == 1
    assert event.timing is EarningsTiming.AFTER_MARKET
    assert event.observed_at is None


def test_decimal_figures_are_preserved_exactly() -> None:
    """A figure a float cannot represent must stay exact through the boundary."""
    event = _event(reported_eps=Decimal("0.1"), reported_revenue=Decimal("123.4500001"))
    assert event.reported_eps == Decimal("0.1")
    assert isinstance(event.reported_eps, Decimal)
    assert event.reported_revenue == Decimal("123.4500001")


@pytest.mark.parametrize(
    "field", ("reported_eps", "estimated_eps", "reported_revenue", "estimated_revenue")
)
def test_figures_must_be_decimal_not_float(field: str) -> None:
    """A float would launder provider precision before the value is ever stored."""
    with pytest.raises(ValueError, match="must be a Decimal"):
        _event(**{field: 1.5})


@pytest.mark.parametrize(
    "field", ("reported_eps", "estimated_eps", "reported_revenue", "estimated_revenue")
)
def test_every_figure_may_be_omitted(field: str) -> None:
    """A scheduled-but-not-yet-reported event, or a vendor that omits a figure,
    is legitimately represented with only some fields present."""
    event = _event(**{field: None})
    assert getattr(event, field) is None


def test_an_event_with_no_figures_at_all_is_legitimate() -> None:
    """A placeholder event (nothing reported, nothing estimated yet) must parse."""
    event = _event(
        reported_eps=None,
        estimated_eps=None,
        reported_revenue=None,
        estimated_revenue=None,
        currency=None,
    )
    assert event.reported_eps is None
    assert event.currency is None


def test_negative_eps_and_revenue_are_accepted() -> None:
    """A loss quarter reports negative EPS; a restatement can move revenue below zero."""
    event = _event(reported_eps=Decimal("-2.31"), reported_revenue=Decimal("-55.00"))
    assert event.reported_eps == Decimal("-2.31")
    assert event.reported_revenue == Decimal("-55.00")


def test_non_finite_figures_are_rejected() -> None:
    with pytest.raises(ValueError, match="finite"):
        _event(reported_eps=Decimal("NaN"))


@pytest.mark.parametrize("timing", list(EarningsTiming))
def test_every_earnings_timing_value_is_accepted(timing: EarningsTiming) -> None:
    event = _event(timing=timing)
    assert event.timing is timing


def test_a_raw_string_is_not_a_valid_timing() -> None:
    """Only the typed vocabulary may cross the boundary."""
    with pytest.raises(ValueError, match="EarningsTiming"):
        _event(timing="BEFORE_MARKET")


def test_naive_event_timestamp_is_rejected() -> None:
    """A naive instant would be reinterpreted in the session time zone by a
    TIMESTAMPTZ column; the guess is refused here, not made."""
    with pytest.raises(ValueError, match="event_timestamp must be timezone-aware"):
        _event(event_timestamp=datetime(2026, 5, 1, 20, 30))


def test_event_timestamp_is_the_event_not_the_ingestion() -> None:
    """The timestamp names when the event occurred, regardless of when we learn it."""
    event = _event(event_timestamp=datetime(2024, 1, 31, 21, 0, tzinfo=UTC))
    assert event.event_timestamp == datetime(2024, 1, 31, 21, 0, tzinfo=UTC)


def test_observed_at_is_optional() -> None:
    assert _event(observed_at=None).observed_at is None


def test_event_date_is_required() -> None:
    """Every earnings-calendar source integrated so far states a calendar
    date; the dataclass must not construct without one."""
    with pytest.raises(TypeError):
        _event_missing_date()


def _event_missing_date() -> CanonicalEarningsEvent:
    fields = {
        "ticker": "AAPL",
        "fiscal_year": 2026,
        "fiscal_quarter": 1,
        "timing": EarningsTiming.AFTER_MARKET,
    }
    return CanonicalEarningsEvent(**fields)  # type: ignore[arg-type]


def test_event_date_must_be_a_plain_date_not_a_datetime() -> None:
    """A ``datetime`` is a ``date`` subclass, so it would otherwise pass an
    ``isinstance`` check while silently carrying a hidden time-of-day that
    contradicts ``event_date``'s own "plain calendar date" contract."""
    with pytest.raises(ValueError, match="event_date must be a plain date"):
        _event(event_date=datetime(2026, 5, 1, 12, 0, tzinfo=UTC))


def test_event_timestamp_is_optional_for_a_date_only_observation() -> None:
    """A source that states only a calendar date -- no exact instant -- must
    be representable, not rejected for lacking a field it never had."""
    event = _event(event_timestamp=None)
    assert event.event_timestamp is None
    assert event.event_date == date(2026, 5, 1)


def test_observed_at_must_be_aware_when_supplied() -> None:
    event = _event(observed_at=datetime(2026, 5, 1, 20, 45, tzinfo=UTC))
    assert event.observed_at == datetime(2026, 5, 1, 20, 45, tzinfo=UTC)
    with pytest.raises(ValueError, match="observed_at must be timezone-aware"):
        _event(observed_at=datetime(2026, 5, 1, 20, 45))


def test_fiscal_year_and_quarter_bounds_are_enforced() -> None:
    with pytest.raises(ValueError, match="fiscal_year"):
        _event(fiscal_year=1899)
    with pytest.raises(ValueError, match="fiscal_year"):
        _event(fiscal_year=2201)
    with pytest.raises(ValueError, match="fiscal_quarter"):
        _event(fiscal_quarter=0)
    with pytest.raises(ValueError, match="fiscal_quarter"):
        _event(fiscal_quarter=5)
    assert _event(fiscal_quarter=None).fiscal_quarter is None


def test_currency_is_an_iso_4217_code_when_present() -> None:
    for bad in ("usd", "USDX", "US", ""):
        with pytest.raises(ValueError, match="currency"):
            _event(currency=bad)
    assert _event(currency=None).currency is None
    assert _event(currency="EUR").currency == "EUR"


def test_blank_ticker_is_refused() -> None:
    with pytest.raises(ValueError, match="non-blank ticker"):
        _event(ticker="   ")


def test_no_provider_specific_fields_leak_into_the_canonical_event() -> None:
    """The canonical type names facts, not a vendor's JSON keys.

    Only canonical, self-describing field names may exist. A provider vocabulary
    slipping into the signature would put vendor names above the ingestion
    boundary for every future adapter to trip on.
    """
    provider_vocabulary = {"o", "h", "l", "c", "v", "vw", "n", "T", "t", "figi", "primary_exchange"}
    field_names = {f.name for f in CanonicalEarningsEvent.__dataclass_fields__.values()}
    assert not (field_names & provider_vocabulary)
