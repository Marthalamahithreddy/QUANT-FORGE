"""Unit tests for the Pydantic v2 boundary schemas.

The database ``CHECK`` constraints are the authority for code shapes and
interval ordering; these validators exist so a bad value is rejected at the
application edge with a readable message instead of surfacing later as an
``IntegrityError``. These tests pin the boundary behaviour, and
``tests/integration/`` separately pins the database's.

No database, no network, no repository: everything here is pure validation.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta, timezone
from datetime import timedelta as dt_timedelta
from datetime import tzinfo as dt_tzinfo
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from annotated_types import Le, SupportsLe
from pydantic import BaseModel, ValidationError

from quantforge.models.base import MAX_FISCAL_YEAR, MIN_FISCAL_YEAR
from quantforge.models.enums import (
    EarningsTiming,
    IdentifierType,
    PriceBasis,
    ProviderType,
    SecurityType,
)
from quantforge.schemas.benchmark import BenchmarkCreate, BenchmarkPriceBarCreate
from quantforge.schemas.common import as_utc_aware, blank_to_none
from quantforge.schemas.company import CompanyCreate
from quantforge.schemas.company import _today as _company_today
from quantforge.schemas.earnings import EarningsEventCreate
from quantforge.schemas.market import DailyPriceBarCreate, DailyPriceBarRead
from quantforge.schemas.provenance import DataSourceCreate
from quantforge.schemas.security import (
    ExchangeCreate,
    ListingCreate,
    SecurityCreate,
    SecurityIdentifierCreate,
)

# ─────────────────────────── shared helpers ───────────────────────────────────


def _valid_company(**overrides: object) -> CompanyCreate:
    payload: dict[str, object] = {
        "legal_name": "Apple Inc.",
        "display_name": "Apple",
        "country_code": "US",
    }
    payload.update(overrides)
    return CompanyCreate(**payload)


def _valid_exchange(**overrides: object) -> ExchangeCreate:
    payload: dict[str, object] = {
        "mic": "XNAS",
        "name": "Nasdaq Stock Market",
        "timezone": "America/New_York",
    }
    payload.update(overrides)
    return ExchangeCreate(**payload)


def _valid_listing(**overrides: object) -> ListingCreate:
    payload: dict[str, object] = {
        "security_id": uuid4(),
        "exchange_id": uuid4(),
        "ticker": "AAPL",
        "valid_from": date(1980, 12, 12),
    }
    payload.update(overrides)
    return ListingCreate(**payload)


def _valid_bar(**overrides: object) -> DailyPriceBarCreate:
    payload: dict[str, object] = {
        "listing_id": uuid4(),
        "trading_date": date(2024, 1, 2),
        "open": Decimal("100.000000"),
        "high": Decimal("105.000000"),
        "low": Decimal("99.000000"),
        "close": Decimal("103.250000"),
        "price_basis": PriceBasis.SPLIT_ADJUSTED,
        "source_id": uuid4(),
    }
    payload.update(overrides)
    return DailyPriceBarCreate(**payload)


# ─────────────────────────── blank_to_none ────────────────────────────────────


@pytest.mark.parametrize("value", ["", "   ", "\t\n"])
def test_blank_strings_collapse_to_none(value: str) -> None:
    """Sources send ``""`` for "not disclosed". Storing ``""`` in a nullable code
    column would satisfy a length constraint while making the row look
    populated."""
    assert blank_to_none(value) is None


def test_blank_to_none_passes_none_through() -> None:
    assert blank_to_none(None) is None


def test_blank_to_none_preserves_meaningful_text() -> None:
    assert blank_to_none("  Apple  ") == "Apple"


# ─────────────────────────── as_utc_aware ─────────────────────────────────────


def test_as_utc_aware_rejects_naive_datetimes() -> None:
    """A naive datetime handed to ``TIMESTAMPTZ`` is interpreted in the
    *session's* time zone, so the stored instant would depend on deployment
    configuration and on DST rules."""
    with pytest.raises(ValueError, match="timezone-aware"):
        as_utc_aware(datetime(2024, 1, 2, 15, 30))


def test_as_utc_aware_normalises_a_non_utc_offset_to_utc() -> None:
    """Same instant, one representation -- otherwise two rows written in
    different offsets would not compare equal."""
    eastern = timezone(timedelta(hours=-5))
    value = as_utc_aware(datetime(2024, 1, 2, 10, 30, tzinfo=eastern))
    assert value == datetime(2024, 1, 2, 15, 30, tzinfo=UTC)
    assert value.tzinfo is UTC


def test_as_utc_aware_preserves_an_instant_expressed_in_utc() -> None:
    moment = datetime(2024, 1, 2, 15, 30, tzinfo=UTC)
    assert as_utc_aware(moment) == moment


def test_as_utc_aware_rejects_a_tzinfo_that_offers_no_offset() -> None:
    """``tzinfo`` is attached but returns ``None``, so the value is still naive
    in every way that matters. The guard checks the resolved offset, not just
    the presence of the attribute."""

    class NoOffset(dt_tzinfo):
        def utcoffset(self, value: datetime | None) -> dt_timedelta | None:
            return None

        def dst(self, value: datetime | None) -> dt_timedelta | None:
            return None

        def tzname(self, value: datetime | None) -> str | None:
            return None

    labelled = datetime(2024, 1, 2, 15, 30).replace(tzinfo=NoOffset())
    with pytest.raises(ValueError, match="timezone-aware"):
        as_utc_aware(labelled)


# ─────────────────────────── country codes ────────────────────────────────────


@pytest.mark.parametrize("code", ["US", "GB", "JP", "DE", "ZZ"])
def test_valid_country_codes_are_accepted(code: str) -> None:
    assert _valid_company(country_code=code).country_code == code


@pytest.mark.parametrize("code", ["us", "Us", "uS", "u1", "1a", "U_", "ÜS"])
def test_wrongly_shaped_country_codes_are_rejected(code: str) -> None:
    with pytest.raises(ValidationError, match="ISO 3166-1"):
        _valid_company(country_code=code)


def test_country_code_may_be_omitted() -> None:
    assert _valid_company(country_code=None).country_code is None
    assert _valid_company(country_code="").country_code is None


def test_a_lowercase_country_code_is_rejected_rather_than_silently_upcased() -> None:
    """Auto-uppercasing would be friendlier, but it would also accept ``"usa"``
    as if it meant ``"US"``. A wrong-but-plausible code is worse than a
    rejection, so the boundary demands the canonical form."""
    with pytest.raises(ValidationError):
        _valid_company(country_code="usa")


# ─────────────────────────── currency codes ───────────────────────────────────


def test_valid_currency_is_accepted() -> None:
    assert _valid_company().model_dump()["country_code"] == "US"
    security = SecurityCreate(company_id=uuid4(), name="Common", currency="USD")
    assert security.currency == "USD"


@pytest.mark.parametrize("code", ["usd", "Usd", "US", "USDD", "US1", "12"])
def test_wrongly_shaped_currency_codes_are_rejected(code: str) -> None:
    with pytest.raises(ValidationError, match="ISO 4217"):
        SecurityCreate(company_id=uuid4(), name="Common", currency=code)


# ─────────────────────────── MIC ──────────────────────────────────────────────


@pytest.mark.parametrize("mic", ["XNAS", "XNYS", "XLON", "XTKS", "XETR"])
def test_valid_mic_codes_are_accepted(mic: str) -> None:
    assert _valid_exchange(mic=mic).mic == mic


@pytest.mark.parametrize("mic", ["xnas", "Xnas", "XNA", "XNYSX", "XN-S", "X_NY"])
def test_wrongly_shaped_mic_codes_are_rejected(mic: str) -> None:
    with pytest.raises(ValidationError, match="ISO 10383"):
        _valid_exchange(mic=mic)


def test_mic_may_contain_digits() -> None:
    """ISO 10383 permits digits, so a purely numeric code is well-formed."""
    assert _valid_exchange(mic="X123").mic == "X123"


# ─────────────────────────── timezones ────────────────────────────────────────


@pytest.mark.parametrize(
    "zone",
    [
        "America/New_York",
        "Europe/London",
        "Asia/Tokyo",
        "UTC",
        "Australia/Sydney",
        # A legacy POSIX-style zone that is nonetheless a real tz database entry,
        # so it must be accepted -- the boundary checks the database, not style.
        "EST5EDT",
    ],
)
def test_iana_timezones_are_accepted(zone: str) -> None:
    assert _valid_exchange(timezone=zone).timezone == zone


@pytest.mark.parametrize("zone", ["-05:00", "+00:00", "Mars/Olympus", "utc", "US/Eastern-5"])
def test_non_iana_timezones_are_rejected(zone: str) -> None:
    with pytest.raises(ValidationError, match="timezone"):
        _valid_exchange(timezone=zone)


def test_a_fixed_utc_offset_is_rejected_explicitly() -> None:
    """``"-05:00"`` is a valid *input* to ``astimezone``, which is exactly why it
    has to be rejected here: an offset is wrong for half of every year."""
    with pytest.raises(ValidationError, match="never a fixed UTC offset"):
        _valid_exchange(timezone="-05:00")


def test_timezone_may_not_be_blank() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        _valid_exchange(timezone="  ")


# ─────────────────────────── company ──────────────────────────────────────────


def test_company_rejects_a_future_founding_date() -> None:
    """A founding date in the future is always a data-entry error."""
    with pytest.raises(ValidationError, match="future"):
        _valid_company(founded_date=_company_today() + timedelta(days=1))


def test_company_accepts_today_as_a_founding_date() -> None:
    """Accepted against the schema's own canonical "today", not the host
    machine's local ``date.today()`` -- those two disagree for part of every
    day in any timezone ahead of UTC, which is the bug this pins against.
    """
    today = _company_today()
    assert _valid_company(founded_date=today).founded_date == today


def test_company_founding_date_boundary_is_exact_and_deterministic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the exact boundary against a frozen canonical clock.

    Independent of the real wall clock and the host's timezone, so this
    cannot flake near a day boundary the way a ``date.today()``-based test
    could.
    """
    frozen = date(2026, 6, 15)
    monkeypatch.setattr("quantforge.schemas.company._today", lambda: frozen)

    assert _valid_company(founded_date=frozen).founded_date == frozen
    assert _valid_company(
        founded_date=frozen - timedelta(days=1)
    ).founded_date == frozen - timedelta(days=1)
    with pytest.raises(ValidationError, match="future"):
        _valid_company(founded_date=frozen + timedelta(days=1))


def test_company_accepts_no_founding_date() -> None:
    assert _valid_company(founded_date=None).founded_date is None


def test_company_rejects_an_unknown_field() -> None:
    """``extra="forbid"`` turns a typo in an ingestion payload into a loud
    failure instead of a silently dropped column."""
    with pytest.raises(ValidationError):
        _valid_company(tikr="AAPL")


def test_company_name_is_stripped() -> None:
    assert _valid_company(display_name="  Apple  ").display_name == "Apple"


def test_company_name_may_not_be_empty() -> None:
    with pytest.raises(ValidationError):
        _valid_company(legal_name="   ")


def test_company_name_length_is_bounded() -> None:
    with pytest.raises(ValidationError):
        _valid_company(legal_name="x" * 256)


# ─────────────────────────── listing validity ─────────────────────────────────


def test_ticker_is_canonicalised_to_upper_case() -> None:
    """This is what guarantees ``aapl`` and ``AAPL`` cannot become two different
    listings, which the database ``CHECK`` requires."""
    assert _valid_listing(ticker="aapl").ticker == "AAPL"
    assert _valid_listing(ticker="  msft  ").ticker == "MSFT"


def test_ticker_may_not_be_blank() -> None:
    with pytest.raises(ValidationError, match="ticker"):
        _valid_listing(ticker="   ")


def test_open_ended_listing_is_allowed() -> None:
    listing = _valid_listing(valid_from=date(2020, 1, 2), valid_to=None)
    assert listing.valid_to is None


def test_adjacent_periods_may_share_an_endpoint() -> None:
    """The interval is half-open ``[valid_from, valid_to)``, so a clean ticker
    handover is two rows sharing one boundary date -- not an overlap."""
    first = _valid_listing(ticker="OLD", valid_from=date(2015, 1, 2), valid_to=date(2020, 1, 2))
    second = _valid_listing(ticker="NEW", valid_from=date(2020, 1, 2), valid_to=None)
    assert first.valid_to == second.valid_from


def test_inverted_listing_interval_is_rejected() -> None:
    with pytest.raises(ValidationError, match="half-open"):
        _valid_listing(valid_from=date(2020, 1, 2), valid_to=date(2019, 1, 2))


def test_zero_length_listing_interval_is_rejected() -> None:
    """``valid_to == valid_from`` is an empty half-open interval, which is never
    what the caller means."""
    with pytest.raises(ValidationError, match="half-open"):
        _valid_listing(valid_from=date(2020, 1, 2), valid_to=date(2020, 1, 2))


def test_security_type_defaults_to_equity() -> None:
    security = SecurityCreate(company_id=uuid4(), name="Common", currency="USD")
    assert security.security_type is SecurityType.EQUITY


def test_security_type_accepts_every_declared_member() -> None:
    for member in SecurityType:
        security = SecurityCreate(
            company_id=uuid4(), name="Instrument", currency="USD", security_type=member
        )
        assert security.security_type is member


def test_unknown_security_type_is_rejected() -> None:
    with pytest.raises(ValidationError):
        SecurityCreate(
            company_id=uuid4(), name="Instrument", currency="USD", security_type="WARRANT"
        )


# ─────────────────────────── identifiers ──────────────────────────────────────


def _valid_identifier(**overrides: object) -> SecurityIdentifierCreate:
    payload: dict[str, object] = {
        "security_id": uuid4(),
        "identifier_type": IdentifierType.ISIN,
        "identifier_value": "US0378331005",
    }
    payload.update(overrides)
    return SecurityIdentifierCreate(**payload)


def test_identifier_may_have_no_validity_dates() -> None:
    """A source may never disclose an identifier's effective dates, so both
    endpoints are optional."""
    identifier = _valid_identifier(valid_from=None, valid_to=None)
    assert identifier.valid_from is None
    assert identifier.valid_to is None


def test_identifier_with_a_valid_to_must_also_have_a_valid_from() -> None:
    """An interval unbounded below cannot satisfy the ordering rule, so this is
    caught at the boundary with a specific message."""
    with pytest.raises(ValidationError, match="valid_from is required"):
        _valid_identifier(valid_from=None, valid_to=date(2020, 1, 2))


def test_inverted_identifier_interval_is_rejected() -> None:
    with pytest.raises(ValidationError, match="strictly after"):
        _valid_identifier(valid_from=date(2020, 1, 2), valid_to=date(2019, 1, 2))


def test_zero_length_identifier_interval_is_rejected() -> None:
    with pytest.raises(ValidationError, match="strictly after"):
        _valid_identifier(valid_from=date(2020, 1, 2), valid_to=date(2020, 1, 2))


def test_identifier_value_may_not_be_blank() -> None:
    with pytest.raises(ValidationError, match="identifier_value"):
        _valid_identifier(identifier_value="   ")


def test_every_identifier_scheme_is_accepted() -> None:
    for member in IdentifierType:
        identifier = _valid_identifier(identifier_type=member, identifier_value="ABC123")
        assert identifier.identifier_type is member


# ─────────────────────────── OHLC coherence ───────────────────────────────────


def test_coherent_ohlc_is_accepted() -> None:
    bar = _valid_bar()
    assert (bar.open, bar.high, bar.low, bar.close) == (
        Decimal("100.000000"),
        Decimal("105.000000"),
        Decimal("99.000000"),
        Decimal("103.250000"),
    )


def test_flat_session_is_coherent() -> None:
    """A limit day prints open == high == low == close, and must not be rejected."""
    bar = _valid_bar(
        open=Decimal("10.000000"),
        high=Decimal("10.000000"),
        low=Decimal("10.000000"),
        close=Decimal("10.000000"),
    )
    assert bar.close == Decimal("10.000000")


@pytest.mark.parametrize(
    ("overrides", "needle"),
    [
        ({"high": Decimal("98.000000")}, "high .* must be >= low"),
        ({"high": Decimal("98.500000")}, "high .* must be >= low"),
        ({"open": Decimal("106.000000")}, "high .* must be >= open"),
        ({"close": Decimal("106.000000")}, "high .* must be >= close"),
        ({"low": Decimal("100.500000")}, "low .* must be <= open"),
        ({"close": Decimal("98.000000")}, "low .* must be <= close"),
    ],
)
def test_incoherent_ohlc_is_rejected_naming_the_broken_bound(
    overrides: dict[str, Decimal], needle: str
) -> None:
    """All five implied bounds are checked so the error names the relationship
    that broke rather than reporting a generic constraint failure."""
    with pytest.raises(ValidationError, match=needle):
        _valid_bar(**overrides)


def test_negative_prices_are_rejected() -> None:
    with pytest.raises(ValidationError):
        _valid_bar(low=Decimal("-1.000000"))


def test_volume_may_be_zero() -> None:
    """A halted session legitimately reports zero volume."""
    assert _valid_bar(volume=0).volume == 0


def test_volume_may_be_unknown() -> None:
    """A source that does not report volume is not the same as a session with no
    trading, so NULL is allowed."""
    assert _valid_bar(volume=None).volume is None


def test_negative_volume_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _valid_bar(volume=-1)


def test_sub_cent_prices_keep_their_precision() -> None:
    """Feeds quote fractional cents; rounding them to cents here would silently
    destroy information before the database ever sees the value."""
    bar = _valid_bar(
        open=Decimal("0.000001"),
        high=Decimal("0.300000"),
        low=Decimal("0.000001"),
        close=Decimal("0.123456"),
    )
    assert bar.open == Decimal("0.000001")
    assert bar.close == Decimal("0.123456")


def test_prices_are_not_coerced_through_a_float() -> None:
    """Parsing through ``float`` would turn 0.1 into 0.1000000000000000055."""
    bar = _valid_bar(
        open=Decimal("0.1"),
        high=Decimal("0.3"),
        low=Decimal("0.1"),
        close=Decimal("0.1"),
    )
    assert bar.close == Decimal("0.1")
    assert str(bar.close) == "0.1"


# ────────────────────── magnitude is the database's decision ──────────────────
#
# The boundary used to carry a hand-picked ceiling (1e9 for prices, 1e12 for
# index levels) on the theory that a larger number must be a mis-scaled feed.
# That is a guess about the future dressed up as validation: it was several
# orders of magnitude below what the column already allows, so it could only
# ever reject legitimate data, never protect the database. Magnitude is now
# bounded by ``NUMERIC`` precision and scale alone.


#: ``NUMERIC(20, 6)``: 14 integer digits and 6 fractional digits.
_NUMERIC_20_6_MAX = Decimal("99999999999999.999999")

_PRICE_FIELDS = ("open", "high", "low", "close", "adjusted_close")
_EARNINGS_AMOUNT_FIELDS = (
    "reported_eps",
    "estimated_eps",
    "reported_revenue",
    "estimated_revenue",
)


def _upper_bounds_of(model: type[BaseModel], fields: tuple[str, ...]) -> dict[str, SupportsLe]:
    """Return explicit numeric upper bounds declared on ``fields`` of ``model``.

    Read from the field metadata rather than by probing values, so that
    re-introducing a ceiling is caught even when the chosen number is large
    enough that no test value trips it.
    """
    found: dict[str, SupportsLe] = {}
    for name in fields:
        for constraint in model.model_fields[name].metadata:
            if isinstance(constraint, Le):
                found[name] = constraint.le
    return found


def test_price_schema_declares_no_arbitrary_upper_bound() -> None:
    assert _upper_bounds_of(DailyPriceBarCreate, _PRICE_FIELDS) == {}


def test_benchmark_level_schema_declares_no_arbitrary_upper_bound() -> None:
    assert _upper_bounds_of(BenchmarkPriceBarCreate, ("close",)) == {}


def test_earnings_amounts_declare_no_arbitrary_bounds() -> None:
    assert _upper_bounds_of(EarningsEventCreate, _EARNINGS_AMOUNT_FIELDS) == {}


def test_db_backed_period_bounds_are_kept() -> None:
    """The upper bounds that legitimately remain are the ones PostgreSQL
    enforces itself, via ``fiscal_year_range`` and ``fiscal_quarter_range``.

    They are duplicated here only so the error names the field, which is what
    distinguishes them from the magnitude ceilings just removed.
    """
    assert _upper_bounds_of(EarningsEventCreate, ("fiscal_year", "fiscal_quarter")) == {
        "fiscal_year": Decimal(MAX_FISCAL_YEAR),
        "fiscal_quarter": Decimal(4),
    }


def test_a_price_at_the_numeric_maximum_is_accepted() -> None:
    """The largest value ``NUMERIC(20, 6)`` can hold must pass the boundary.

    Anything less than this is the database's call to make, not the schema's.
    """
    bar = _valid_bar(
        open=_NUMERIC_20_6_MAX,
        high=_NUMERIC_20_6_MAX,
        low=_NUMERIC_20_6_MAX,
        close=_NUMERIC_20_6_MAX,
        adjusted_close=_NUMERIC_20_6_MAX,
    )
    assert bar.close == _NUMERIC_20_6_MAX


def test_the_value_the_removed_ceiling_rejected_is_now_accepted() -> None:
    """The exact payload the old 1e9 bound rejected.

    Kept as a literal so the regression is visible: if a ceiling is ever
    reintroduced below this, this test fails rather than the old one.
    """
    bar = _valid_bar(
        open=Decimal("1000000001.000000"),
        high=Decimal("1000000002.000000"),
        low=Decimal("1000000000.000000"),
        close=Decimal("1000000001.000000"),
    )
    assert bar.open == Decimal("1000000001.000000")


def test_a_very_large_benchmark_level_is_accepted() -> None:
    bar = BenchmarkPriceBarCreate(
        price_basis=PriceBasis.SPLIT_ADJUSTED,
        benchmark_id=uuid4(),
        trading_date=date(2024, 1, 2),
        close=_NUMERIC_20_6_MAX,
        source_id=uuid4(),
    )
    assert bar.close == _NUMERIC_20_6_MAX


def test_a_negative_price_is_still_rejected() -> None:
    """The lower bound is a real fact, not a guess, and the database agrees.

    ``close_non_negative`` and friends enforce the same thing, so this is the
    boundary mirroring a ``CHECK`` rather than inventing a rule.
    """
    with pytest.raises(ValidationError):
        _valid_bar(low=Decimal("-0.000001"), open=Decimal("-1"), close=Decimal("-1"))
    with pytest.raises(ValidationError):
        _valid_bar(high=Decimal("-1"))


def test_a_negative_benchmark_level_is_still_rejected() -> None:
    with pytest.raises(ValidationError):
        BenchmarkPriceBarCreate(
            price_basis=PriceBasis.SPLIT_ADJUSTED,
            benchmark_id=uuid4(),
            trading_date=date(2024, 1, 2),
            close=Decimal("-0.000001"),
            source_id=uuid4(),
        )


def test_negative_and_large_earnings_figures_are_accepted() -> None:
    """A loss is a real reported figure, and the database constrains neither.

    ``earnings_event`` has no sign or magnitude ``CHECK`` on these columns, so
    the schema must not invent one. EPS fits ``NUMERIC(20, 4)`` and revenue
    ``NUMERIC(24, 4)``, which is the only limit either layer applies.
    """
    event = EarningsEventCreate(
        company_id=uuid4(),
        source_id=uuid4(),
        event_date=date(2024, 1, 2),
        event_timestamp=datetime(2024, 1, 2, tzinfo=UTC),
        timing=EarningsTiming.AFTER_MARKET,
        fiscal_year=2024,
        fiscal_quarter=1,
        reported_eps=Decimal("-99999.9999"),
        estimated_eps=Decimal("-99999.9999"),
        reported_revenue=Decimal("-99999999999999.9999"),
        estimated_revenue=Decimal("-99999999999999.9999"),
    )
    assert event.reported_eps == Decimal("-99999.9999")
    assert event.reported_revenue == Decimal("-99999999999999.9999")


# ─────────────────────────── observation timestamps ───────────────────────────


def test_observed_at_may_be_unknown() -> None:
    """Many daily feeds publish no timestamp. ``None`` is the honest value; the
    source did not say, and inventing a time would corrupt a point-in-time
    query more than a gap would."""
    assert _valid_bar(observed_at=None).observed_at is None


def test_ingested_at_defaults_to_now_as_utc() -> None:
    bar = _valid_bar()
    assert bar.ingested_at.tzinfo is not None
    assert bar.ingested_at <= datetime.now(UTC)


@pytest.mark.parametrize("field", ["observed_at", "ingested_at"])
def test_naive_timestamps_are_rejected(field: str) -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        _valid_bar(**{field: datetime(2024, 1, 2, 16, 0)})


def test_non_utc_timestamps_are_normalised_to_utc() -> None:
    bar = _valid_bar(observed_at=datetime(2024, 1, 2, 11, 0, tzinfo=timezone(timedelta(hours=-5))))
    assert bar.observed_at == datetime(2024, 1, 2, 16, 0, tzinfo=UTC)


def test_observed_at_and_ingested_at_are_independent() -> None:
    """A backfilled bar is observed long before it is ingested; conflating them
    would make every historical import look like fresh information."""
    bar = _valid_bar(observed_at=datetime(1990, 1, 2, tzinfo=UTC))
    assert bar.observed_at is not None
    assert bar.ingested_at > datetime(2024, 1, 1, tzinfo=UTC)


# ─────────────────────────── serialisation ────────────────────────────────────


def _read_bar_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": uuid4(),
        "listing_id": uuid4(),
        "trading_date": date(2024, 1, 2),
        "open": Decimal("100.000000"),
        "high": Decimal("105.000000"),
        "low": Decimal("99.000000"),
        "close": Decimal("0.1"),
        "price_basis": PriceBasis.SPLIT_ADJUSTED,
        "adjusted_close": None,
        "volume": 1_000_000,
        "source_id": uuid4(),
        "observed_at": None,
        "ingested_at": datetime(2024, 1, 2, 21, 0, tzinfo=UTC),
        "created_at": datetime(2024, 1, 2, 21, 0, tzinfo=UTC),
    }
    payload.update(overrides)
    return payload


def test_decimal_prices_serialise_as_json_strings() -> None:
    """Pydantic emits ``Decimal`` as a string in JSON mode precisely so a client
    cannot re-parse 0.1 as a binary float. Silently changing this would be a
    correctness regression in a research system."""
    payload = json.loads(DailyPriceBarRead(**_read_bar_payload()).model_dump_json())
    assert payload["close"] == "0.1"
    assert payload["open"] == "100.000000"
    assert isinstance(payload["close"], str)


def test_null_adjusted_close_stays_null_in_json() -> None:
    payload = json.loads(DailyPriceBarRead(**_read_bar_payload()).model_dump_json())
    assert payload["adjusted_close"] is None


def test_ingested_at_serialises_with_an_explicit_offset() -> None:
    payload = json.loads(DailyPriceBarRead(**_read_bar_payload()).model_dump_json())
    assert payload["ingested_at"].startswith("2024-01-02T21:00:00")


def test_read_schema_is_frozen() -> None:
    """A read model handed to a caller must not be mutable in place."""
    bar = DailyPriceBarRead(**_read_bar_payload())
    with pytest.raises(ValidationError):
        bar.close = Decimal("1")  # type: ignore[misc]


def test_read_schema_hydrates_from_an_orm_instance() -> None:
    class FakeBar:
        id = uuid4()
        listing_id = uuid4()
        trading_date = date(2024, 1, 2)
        open = Decimal("100.000000")
        high = Decimal("105.000000")
        low = Decimal("99.000000")
        close = Decimal("103.250000")
        price_basis = PriceBasis.AS_TRADED
        adjusted_close = None
        volume = 1_000_000
        source_id = uuid4()
        observed_at = None
        ingested_at = datetime(2024, 1, 2, 21, 0, tzinfo=UTC)
        created_at = datetime(2024, 1, 2, 21, 0, tzinfo=UTC)

    bar = DailyPriceBarRead.model_validate(FakeBar())
    assert bar.close == Decimal("103.250000")
    assert bar.price_basis is PriceBasis.AS_TRADED
    assert bar.adjusted_close is None


# ─────────────────────────── benchmarks ───────────────────────────────────────


def test_benchmark_accepts_a_bare_name() -> None:
    """Vendors disagree on symbols, so a benchmark is usable without one."""
    benchmark = BenchmarkCreate(name="S&P 500")
    assert benchmark.symbol is None
    assert benchmark.source_id is None


def test_benchmark_blank_symbol_is_normalised_to_none() -> None:
    """A blank symbol is noise, not a value. Normalising it to ``None`` -- rather
    than rejecting it or storing ``""`` -- keeps the row honest about being
    symbol-less, and matches what the database ``CHECK`` expects."""
    assert BenchmarkCreate(name="S&P 500", symbol="   ").symbol is None
    assert BenchmarkCreate(name="S&P 500", symbol="").symbol is None


def test_benchmark_price_bar_requires_a_positive_close() -> None:
    with pytest.raises(ValidationError):
        BenchmarkPriceBarCreate(
            price_basis=PriceBasis.SPLIT_ADJUSTED,
            benchmark_id=uuid4(),
            trading_date=date(2024, 1, 2),
            close=Decimal("-1.000000"),
            source_id=uuid4(),
        )


def test_benchmark_price_bar_rejects_a_naive_timestamp() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        BenchmarkPriceBarCreate(
            price_basis=PriceBasis.SPLIT_ADJUSTED,
            benchmark_id=uuid4(),
            trading_date=date(2024, 1, 2),
            close=Decimal("4500.000000"),
            source_id=uuid4(),
            observed_at=datetime(2024, 1, 2, 21, 0),
        )


# ─────────────────────────── earnings ──────────────────────────────────────────


def _valid_earnings(**overrides: object) -> EarningsEventCreate:
    payload: dict[str, object] = {
        "company_id": uuid4(),
        "fiscal_year": 2024,
        "fiscal_quarter": 1,
        "event_date": date(2024, 1, 31),
        "event_timestamp": datetime(2024, 1, 31, 21, 0, tzinfo=UTC),
        "source_id": uuid4(),
    }
    payload.update(overrides)
    return EarningsEventCreate(**payload)


def test_earnings_timing_defaults_to_unknown() -> None:
    """A source that does not state the timing must not have one inferred;
    ``UNKNOWN`` keeps the gap visible instead of guessing."""
    assert _valid_earnings().timing is EarningsTiming.UNKNOWN


@pytest.mark.parametrize("timing", list(EarningsTiming))
def test_every_earnings_timing_is_accepted(timing: EarningsTiming) -> None:
    assert _valid_earnings(timing=timing).timing is timing


def test_earnings_event_timestamp_must_be_aware() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        _valid_earnings(event_timestamp=datetime(2024, 1, 31, 21, 0))


def test_earnings_event_timestamp_is_optional() -> None:
    """A date-only observation -- the common case for an earnings-calendar
    source that states no exact instant -- must be accepted, not rejected for
    lacking a field the source never had."""
    event = _valid_earnings(event_timestamp=None)
    assert event.event_timestamp is None
    assert event.event_date == date(2024, 1, 31)


def test_earnings_event_date_is_required() -> None:
    with pytest.raises(ValidationError, match="event_date"):
        _valid_earnings(event_date=None)


def test_annual_reporting_has_no_quarter() -> None:
    """``fiscal_quarter=None`` is a full-year figure, which is a real case and
    must not be confused with a missing quarter."""
    event = _valid_earnings(fiscal_quarter=None)
    assert event.fiscal_quarter is None


@pytest.mark.parametrize("quarter", [0, 5, -1])
def test_out_of_range_fiscal_quarters_are_rejected(quarter: int) -> None:
    with pytest.raises(ValidationError):
        _valid_earnings(fiscal_quarter=quarter)


@pytest.mark.parametrize("fiscal_year", [MIN_FISCAL_YEAR - 1, MAX_FISCAL_YEAR + 1, 0, -5])
def test_out_of_range_fiscal_years_are_rejected(fiscal_year: int) -> None:
    with pytest.raises(ValidationError):
        _valid_earnings(fiscal_year=fiscal_year)


def test_boundary_fiscal_years_are_accepted() -> None:
    assert _valid_earnings(fiscal_year=MIN_FISCAL_YEAR).fiscal_year == MIN_FISCAL_YEAR
    assert _valid_earnings(fiscal_year=MAX_FISCAL_YEAR).fiscal_year == MAX_FISCAL_YEAR


def test_negative_eps_is_allowed() -> None:
    """A loss is a real reported figure, not a data error."""
    event = _valid_earnings(reported_eps=Decimal("-0.2500"), estimated_eps=Decimal("-0.1000"))
    assert event.reported_eps == Decimal("-0.2500")


def test_earnings_figures_may_all_be_unknown() -> None:
    """A source may publish a timestamp and nothing else."""
    event = _valid_earnings()
    assert event.reported_eps is None
    assert event.estimated_eps is None
    assert event.reported_revenue is None
    assert event.estimated_revenue is None


def test_earnings_schemas_expose_no_derived_analytics() -> None:
    """Surprise, tradability and returns are *derived*, and deriving them in the
    boundary schema would bake in an assumption about the source that cannot be
    revisited later."""
    forbidden = {"surprise", "surprise_pct", "is_tradable", "return", "abnormal_return", "car"}
    assert not forbidden & set(EarningsEventCreate.model_fields)


# ─────────────────────────── provenance ───────────────────────────────────────


def test_data_source_accepts_an_https_url() -> None:
    source = DataSourceCreate(
        name="Exchange Feed",
        provider_type=ProviderType.EXCHANGE_FEED,
        base_url="https://feed.example.com",
    )
    assert source.base_url == "https://feed.example.com"


@pytest.mark.parametrize(
    "base_url", ["ftp://feed.example.com", "file:///etc/passwd", "example.com"]
)
def test_data_source_rejects_a_non_http_base_url(base_url: str) -> None:
    """An ingestion URL that resolves to a local file is an SSRF-shaped problem
    worth refusing at the edge."""
    with pytest.raises(ValidationError):
        DataSourceCreate(
            name="Feed", provider_type=ProviderType.MARKET_DATA_VENDOR, base_url=base_url
        )


def test_data_source_may_have_no_url() -> None:
    assert DataSourceCreate(name="Manual", provider_type=ProviderType.MANUAL).base_url is None


@pytest.mark.parametrize("provider", list(ProviderType))
def test_every_provider_type_is_accepted(provider: ProviderType) -> None:
    assert DataSourceCreate(name="S", provider_type=provider).provider_type is provider


# ─────────────────────────── round-tripping ──────────────────────────────────


def test_uuid_fields_accept_strings_and_normalise_to_uuid() -> None:
    """A JSON payload carries a string id; the boundary must hand repositories a
    real ``UUID`` so the driver never has to guess."""
    raw = uuid4()
    listing = _valid_listing(security_id=str(raw))
    assert isinstance(listing.security_id, UUID)
    assert listing.security_id == raw


def test_a_malformed_uuid_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _valid_listing(security_id="not-a-uuid")


def test_a_trading_date_is_not_silently_made_tz_aware() -> None:
    """A date crossing the boundary must stay a calendar fact."""
    bar = _valid_bar(trading_date="2024-01-02")
    assert bar.trading_date == date(2024, 1, 2)
    assert not isinstance(bar.trading_date, datetime)


# ── price basis is required on both bar schemas ─────────────────────────────


def test_a_daily_bar_payload_without_a_price_basis_is_rejected() -> None:
    payload: dict[str, object] = {
        "listing_id": uuid4(),
        "trading_date": date(2024, 1, 2),
        "open": Decimal("1"),
        "high": Decimal("2"),
        "low": Decimal("1"),
        "close": Decimal("2"),
        "source_id": uuid4(),
    }
    with pytest.raises(ValidationError, match="price_basis"):
        DailyPriceBarCreate(**payload)
    with pytest.raises(ValidationError, match="price_basis"):
        _valid_bar(price_basis="RAW")


def test_a_benchmark_bar_payload_without_a_price_basis_is_rejected() -> None:
    with pytest.raises(ValidationError, match="price_basis"):
        BenchmarkPriceBarCreate(  # type: ignore[call-arg]
            benchmark_id=uuid4(),
            trading_date=date(2024, 1, 2),
            close=Decimal("4500.000000"),
            source_id=uuid4(),
        )


def test_the_read_schema_carries_the_price_basis_as_a_json_string() -> None:
    payload = json.loads(
        DailyPriceBarRead(**_read_bar_payload(price_basis=PriceBasis.AS_TRADED)).model_dump_json()
    )
    assert payload["price_basis"] == "AS_TRADED"
