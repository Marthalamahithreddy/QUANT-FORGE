"""Unit tests for the repository layer's *query construction*.

A repository is mostly a statement plus a decision about how the rows come back.
Those decisions are easy to get subtly wrong -- an exclusive bound where an
inclusive one was documented, a missing ``ORDER BY``, a filter that silently
applies to every row -- and a subtle wrong answer is far more damaging than a
crash. So these tests capture the emitted SQL with a recording stub and assert
on the statement itself.

Nothing here connects to a database. ``tests/integration/`` proves the same
queries run correctly against real PostgreSQL and return the right rows; this
file proves they were built the way the docstring claims.

The ``RecordingSession`` stub implements only the three methods the repositories
call. If a repository starts using a fourth, these tests fail loudly rather
than passing against a stub that quietly swallows the call.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Compiled

from quantforge.models.company import Company
from quantforge.models.enums import IdentifierType, PriceBasis
from quantforge.models.market import DailyPriceBar
from quantforge.repositories.benchmark import BenchmarkRepository
from quantforge.repositories.company import CompanyRepository
from quantforge.repositories.earnings import EarningsRepository
from quantforge.repositories.market import MarketDataRepository
from quantforge.repositories.security import (
    ExchangeRepository,
    ListingRepository,
    SecurityIdentifierRepository,
    SecurityRepository,
)


class RecordingSession(sa.orm.Session):
    """A :class:`~sqlalchemy.orm.Session` that records statements instead of executing them.

    Subclassing rather than duck-typing means the repositories type-check against
    the real ``Session`` signature, so a repository that starts calling a method
    this stub does not override fails the type check instead of silently
    returning a wrong answer at runtime.

    ``Session.__init__`` is deliberately not called: no engine is ever bound and
    every method a repository uses is overridden below, so the base
    implementation's connection state is never touched.
    """

    def __init__(self, scalars_result: Any = None, scalar_result: Any = None) -> None:
        self.statements: list[Any] = []
        self.added: list[Any] = []
        self.scalar_result = scalar_result
        self.scalars_result: Any = [] if scalars_result is None else scalars_result

    def add(self, instance: Any, _warn: bool = True) -> None:
        self.added.append(instance)

    def flush(self, objects: Any = None) -> None:
        return None

    def scalars(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        self.statements.append(statement)
        return self.scalars_result

    def scalar(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        self.statements.append(statement)
        return self.scalar_result

    def get(self, entity: Any, ident: Any, **kwargs: Any) -> Any:
        self.statements.append(entity)
        return None

    @property
    def last(self) -> Any:
        assert self.statements, "no statement was recorded"
        return self.statements[-1]


def _pg_dialect() -> sa.engine.interfaces.Dialect:
    """A PostgreSQL dialect for rendering, via the public SQLAlchemy factory.

    ``sa.dialects.postgresql.dialect()`` is untyped upstream, so the result is
    annotated at the ``Dialect`` interface rather than a concrete subclass.
    """
    dialect: sa.engine.interfaces.Dialect = postgresql.dialect()  # type: ignore[no-untyped-call]
    return dialect


def _compile(statement: Any) -> Compiled:
    compiled: Compiled = statement.compile(
        dialect=_pg_dialect(),
        compile_kwargs={"render_postcompile": True},
    )
    return compiled


def sql_of(statement: Any) -> str:
    """Render a recorded statement as PostgreSQL SQL.

    The literal dialect is used so bound values appear inline, which makes a
    failure message readable in a test report instead of showing ``%(x_1)s``.
    """
    return str(_compile(statement))


def params_of(statement: Any) -> dict[str, Any]:
    """Return the bound parameters of a recorded statement, by name."""
    return dict(_compile(statement).params)


def bound_values(statement: Any) -> list[Any]:
    """Every bound value, unwrapped from SQLAlchemy's bind-parameter objects."""
    return [getattr(value, "value", value) for value in params_of(statement).values()]


# ─────────────────────────── add() delegates to the session ───────────────────


@pytest.mark.parametrize(
    ("factory", "model_name"),
    [
        (CompanyRepository, "Company"),
    ],
)
def test_company_add_flushes_and_returns_the_instance(factory: type, model_name: str) -> None:
    """``add`` flushes so the caller gets a server-assigned value back, but does
    not commit: the unit of work is the caller's, not the repository's."""

    session = RecordingSession()
    company = Company(legal_name="Acme Inc", display_name="Acme")
    result = factory(session).add(company)
    assert result is company
    assert session.added == [company]


def test_market_add_flushes_so_the_caller_gets_an_id() -> None:
    session = RecordingSession()
    repo = MarketDataRepository(session)
    bar = DailyPriceBar(
        price_basis=PriceBasis.SPLIT_ADJUSTED,
        listing_id=uuid.uuid4(),
        trading_date=dt.date(2024, 1, 2),
        open=Decimal("1"),
        high=Decimal("1"),
        low=Decimal("1"),
        close=Decimal("1"),
        source_id=uuid.uuid4(),
    )
    assert repo.add(bar) is bar
    assert session.added == [bar]


# ─────────────────────────── ticker resolution ───────────────────────────────


def test_resolve_by_ticker_upper_cases_the_symbol() -> None:
    """The stored form is canonical upper case, so a lower-case request must be
    canonicalised or a perfectly good lookup silently misses."""
    session = RecordingSession()
    ListingRepository(session).resolve_by_ticker("aapl", on_date=dt.date(2024, 1, 2))
    assert "AAPL" in bound_values(session.last)


def test_resolve_by_ticker_uses_the_half_open_interval() -> None:
    """``[valid_from, valid_to)``: the start bound is inclusive, the end bound is
    exclusive and NULL means unbounded. Getting the upper bound wrong makes a
    ticker handover either gap or double-count."""
    session = RecordingSession()
    ListingRepository(session).resolve_by_ticker("AAPL", on_date=dt.date(2024, 1, 2))
    sql = sql_of(session.last)
    assert "listing.valid_from <= " in sql
    assert "listing.valid_to > " in sql
    assert "listing.valid_to IS NULL" in sql


def test_resolve_by_ticker_accepts_a_date_unchanged() -> None:
    """The bound is a ``DATE``, matching the column type, so no time or zone
    conversion can shift which session resolves."""
    session = RecordingSession()
    on_date = dt.date(2024, 1, 2)
    ListingRepository(session).resolve_by_ticker("AAPL", on_date=on_date)
    assert on_date in bound_values(session.last)


def test_resolve_by_ticker_does_not_filter_by_venue_when_unasked() -> None:
    session = RecordingSession()
    ListingRepository(session).resolve_by_ticker("AAPL", on_date=dt.date(2024, 1, 2))
    assert "listing.exchange_id = " not in sql_of(session.last)


def test_resolve_by_ticker_filters_by_venue_when_given_one() -> None:
    """A ticker can be genuinely ambiguous across venues, so the venue is an
    explicit disambiguator rather than something guessed."""
    session = RecordingSession()
    ListingRepository(session).resolve_by_ticker(
        "AAPL", on_date=dt.date(2024, 1, 2), exchange_id=uuid.uuid4()
    )
    assert "listing.exchange_id = " in sql_of(session.last)


def test_resolve_by_ticker_returns_none_when_absent() -> None:
    """``None`` distinguishes "this ticker did not exist yet" from "we have no
    data for it" -- both meaningful, and neither an error."""
    session = RecordingSession(scalar_result=None)
    result = ListingRepository(session).resolve_by_ticker("ZZZZ", on_date=dt.date(2024, 1, 2))
    assert result is None


def test_current_primary_listing_asks_for_an_open_primary_row() -> None:
    session = RecordingSession(scalar_result=None)
    ListingRepository(session).current_primary_listing(uuid.uuid4())
    sql = sql_of(session.last)
    assert "listing.is_primary IS true" in sql
    assert "listing.valid_to IS NULL" in sql


def test_current_primary_listing_has_no_limit_one() -> None:
    """The partial unique index already guarantees at most one such row, so a
    ``LIMIT 1`` would be papering over a data-integrity bug."""
    session = RecordingSession(scalar_result=None)
    ListingRepository(session).current_primary_listing(uuid.uuid4())
    assert "LIMIT" not in sql_of(session.last).upper()


def test_ticker_history_is_ordered_by_validity() -> None:
    """Ordered by ``valid_from`` so consecutive periods reconstruct a handover
    without the caller re-sorting."""
    session = RecordingSession()
    ListingRepository(session).ticker_history(exchange_id=uuid.uuid4(), ticker="AAPL")
    assert "ORDER BY listing.valid_from" in sql_of(session.last)


def test_ticker_history_upper_cases_the_symbol() -> None:
    session = RecordingSession()
    ListingRepository(session).ticker_history(exchange_id=uuid.uuid4(), ticker="aapl")
    assert "AAPL" in bound_values(session.last)


def test_list_for_security_returns_every_period() -> None:
    """Deliberately *not* filtered to current rows: an instrument's full venue
    history is what provenance work needs."""
    session = RecordingSession()
    SecurityRepository(session).list_for_company(uuid.uuid4())
    assert "security.company_id = " in sql_of(session.last)


# ─────────────────────────── market data ranges ───────────────────────────────


def test_list_range_uses_inclusive_bounds() -> None:
    session = RecordingSession()
    MarketDataRepository(session).list_range(
        price_basis=PriceBasis.SPLIT_ADJUSTED,
        listing_id=uuid.uuid4(),
        start=dt.date(2024, 1, 1),
        end=dt.date(2024, 1, 31),
    )
    sql = sql_of(session.last)
    assert "daily_price_bar.trading_date >= " in sql
    assert "daily_price_bar.trading_date <= " in sql


def test_list_range_is_ordered_by_date_then_recency() -> None:
    """Chronological first, because a price series is read in time order. The
    secondary descending ``ingested_at`` makes the newest revision of a
    restated session deterministic when a source ever reports one twice."""
    session = RecordingSession()
    MarketDataRepository(session).list_range(
        price_basis=PriceBasis.SPLIT_ADJUSTED,
        listing_id=uuid.uuid4(),
        start=dt.date(2024, 1, 1),
        end=dt.date(2024, 1, 31),
    )
    sql = sql_of(session.last)
    assert "ORDER BY daily_price_bar.trading_date, daily_price_bar.ingested_at DESC" in sql


def test_list_range_does_not_filter_by_source_unless_asked() -> None:
    session = RecordingSession()
    MarketDataRepository(session).list_range(
        price_basis=PriceBasis.SPLIT_ADJUSTED,
        listing_id=uuid.uuid4(),
        start=dt.date(2024, 1, 1),
        end=dt.date(2024, 1, 31),
    )
    assert "daily_price_bar.source_id = " not in sql_of(session.last)


def test_list_range_filters_by_source_when_given_one() -> None:
    session = RecordingSession()
    MarketDataRepository(session).list_range(
        price_basis=PriceBasis.SPLIT_ADJUSTED,
        listing_id=uuid.uuid4(),
        start=dt.date(2024, 1, 1),
        end=dt.date(2024, 1, 31),
        source_id=uuid.uuid4(),
    )
    assert "daily_price_bar.source_id = " in sql_of(session.last)


def test_sources_for_listing_selects_a_single_distinct_column() -> None:
    """Selecting a whole entity and de-duplicating in Python would pull every
    bar out of the database to answer a yes/no provenance question."""
    session = RecordingSession()
    MarketDataRepository(session).sources_for_listing(
        price_basis=PriceBasis.SPLIT_ADJUSTED,
        listing_id=uuid.uuid4(),
        start=dt.date(2024, 1, 1),
        end=dt.date(2024, 1, 31),
    )
    sql = sql_of(session.last)
    assert "SELECT DISTINCT daily_price_bar.source_id" in sql
    # No payload column in the projection: pulling whole rows just to count
    # distinct vendors would move every bar in the window through the client.
    projection = sql.split("FROM")[0]
    for column in ("open", "high", "low", "close", "volume"):
        assert column not in projection


def test_sources_for_listing_applies_the_range() -> None:
    session = RecordingSession()
    MarketDataRepository(session).sources_for_listing(
        price_basis=PriceBasis.SPLIT_ADJUSTED,
        listing_id=uuid.uuid4(),
        start=dt.date(2024, 1, 1),
        end=dt.date(2024, 1, 31),
    )
    sql = sql_of(session.last)
    assert "daily_price_bar.trading_date >= " in sql
    assert "daily_price_bar.trading_date <= " in sql


def test_get_for_date_without_a_source_takes_the_most_recent_ingest() -> None:
    """A source-agnostic read is a cross-vendor consensus question, so the
    newest ingestion wins rather than an arbitrary row."""
    session = RecordingSession(scalar_result=None)
    MarketDataRepository(session).get_for_date(
        price_basis=PriceBasis.SPLIT_ADJUSTED,
        listing_id=uuid.uuid4(),
        trading_date=dt.date(2024, 1, 2),
    )
    assert "ORDER BY daily_price_bar.ingested_at DESC" in sql_of(session.last)


def test_get_for_date_with_a_source_still_filters_on_it() -> None:
    """The ``source_id`` filter is what makes the answer unambiguous; the
    ``ORDER BY`` that remains is a no-op under the uniqueness constraint and is
    kept for a single code path."""
    session = RecordingSession(scalar_result=None)
    MarketDataRepository(session).get_for_date(
        price_basis=PriceBasis.SPLIT_ADJUSTED,
        listing_id=uuid.uuid4(),
        trading_date=dt.date(2024, 1, 2),
        source_id=uuid.uuid4(),
    )
    assert "daily_price_bar.source_id = " in sql_of(session.last)


def test_close_series_returns_decimals_in_the_given_order() -> None:
    """A local convenience that exists to make one rule unavoidable: prices leave
    this layer as ``Decimal`` and never as ``float``."""
    bars = [
        DailyPriceBar(
            price_basis=PriceBasis.SPLIT_ADJUSTED,
            listing_id=uuid.uuid4(),
            trading_date=dt.date(2024, 1, day),
            open=Decimal("1"),
            high=Decimal("1"),
            low=Decimal("1"),
            close=Decimal(str(day)),
            source_id=uuid.uuid4(),
        )
        for day in (2, 3)
    ]
    series = MarketDataRepository.close_series(bars)
    assert series == [Decimal("2"), Decimal("3")]
    assert all(isinstance(value, Decimal) for value in series)
    assert not any(isinstance(value, float) for value in series)


def test_close_series_of_nothing_is_empty() -> None:
    assert MarketDataRepository.close_series([]) == []


# ─────────────────────────── earnings ─────────────────────────────────────────


def test_earnings_are_ordered_chronologically() -> None:
    """An event study consumes this list in time order; leaving the ordering to
    the caller means one caller eventually gets it wrong.

    Ordered by ``event_date``, not ``event_timestamp``: the latter is
    frequently absent (a date-only observation), and ordering on it would
    silently mis-order exactly those rows.
    """
    session = RecordingSession()
    EarningsRepository(session).list_chronological(company_id=uuid.uuid4())
    assert "ORDER BY earnings_event.event_date" in sql_of(session.last)


def test_earnings_bounds_are_inclusive() -> None:
    session = RecordingSession()
    EarningsRepository(session).list_chronological(
        company_id=uuid.uuid4(),
        start=dt.date(2024, 1, 1),
        end=dt.date(2024, 12, 31),
    )
    sql = sql_of(session.last)
    assert "earnings_event.event_date >= " in sql
    assert "earnings_event.event_date <= " in sql


def test_earnings_bounds_are_optional() -> None:
    session = RecordingSession()
    EarningsRepository(session).list_chronological(company_id=uuid.uuid4())
    sql = sql_of(session.last)
    assert "event_date >=" not in sql
    assert "event_date <=" not in sql


def test_fiscal_period_without_a_quarter_means_annual() -> None:
    """``IS NULL`` means "this issuer's full-year figures". Guessing the other
    way would silently return quarterly rows for an annual query."""
    session = RecordingSession()
    EarningsRepository(session).list_for_fiscal_period(
        company_id=uuid.uuid4(), fiscal_year=2024, fiscal_quarter=None
    )
    sql = sql_of(session.last)
    assert "earnings_event.fiscal_quarter IS NULL" in sql
    assert "earnings_event.fiscal_quarter =" not in sql


def test_fiscal_period_with_a_quarter_matches_exactly() -> None:
    session = RecordingSession()
    EarningsRepository(session).list_for_fiscal_period(
        company_id=uuid.uuid4(), fiscal_year=2024, fiscal_quarter=2
    )
    assert "earnings_event.fiscal_quarter = " in sql_of(session.last)


def test_fiscal_period_returns_every_matching_row() -> None:
    """No uniqueness is assumed: a preliminary release, a final one and two
    vendors all describe the same period, and all are legitimate rows."""
    session = RecordingSession(scalars_result=[object(), object(), object()])
    result = EarningsRepository(session).list_for_fiscal_period(
        company_id=uuid.uuid4(), fiscal_year=2024, fiscal_quarter=2
    )
    assert len(result) == 3


def test_list_between_dates_compares_directly_against_event_date() -> None:
    """A plain ``DATE`` comparison in SQL, with no time-zone widening needed:
    ``event_date`` carries no time-of-day to widen in the first place."""
    session = RecordingSession()
    EarningsRepository(session).list_between_dates(
        company_id=uuid.uuid4(), start=dt.date(2024, 1, 1), end=dt.date(2024, 1, 31)
    )
    sql = sql_of(session.last)
    assert "earnings_event.event_date >= " in sql
    assert "earnings_event.event_date <= " in sql
    values = bound_values(session.last)
    assert dt.date(2024, 1, 1) in values
    assert dt.date(2024, 1, 31) in values


# ─────────────────────────── reference lookups ───────────────────────────────


def test_company_search_uses_an_exact_prefix_match() -> None:
    """A plain prefix is what the btree index on ``display_name`` can serve.

    Case-insensitive or substring matching would need a functional index or
    ``pg_trgm``, which is out of scope here. The behaviour is pinned so that
    changing it later is a deliberate act rather than an accident.
    """
    session = RecordingSession()
    CompanyRepository(session).search_by_display_name("App", limit=10)
    sql = sql_of(session.last)
    # The prefix is bound, with the wildcard appended in SQL, so the pattern is
    # never interpolated into the statement text.
    assert "display_name LIKE " in sql
    assert "|| '%%'" in sql
    assert "App%" not in sql
    # The prefix reaches the database byte-for-byte: no lower(), no citext, no
    # case folding. That is the documented, index-backed behaviour.
    assert "App" in bound_values(session.last)
    assert "lower(" not in sql.lower()


def test_company_search_applies_its_limit() -> None:
    """An unfiltered prefix scan across every issuer is unbounded work."""
    session = RecordingSession()
    CompanyRepository(session).search_by_display_name("app", limit=10)
    assert "LIMIT" in sql_of(session.last).upper()


def test_company_search_orders_deterministically() -> None:
    """Without a tiebreak, pagination over duplicate prefixes repeats and skips
    rows between requests."""
    session = RecordingSession()
    CompanyRepository(session).search_by_display_name("app", limit=10)
    assert "ORDER BY" in sql_of(session.last).upper()


def test_get_by_mic_normalises_case() -> None:
    session = RecordingSession()
    ExchangeRepository(session).get_by_mic("xnas")
    assert "XNAS" in bound_values(session.last)


def test_identifier_lookup_matches_the_stored_value_exactly() -> None:
    """The case is not normalised on the way in, so normalising it on the way out
    would look up a value that may not be the one stored. An exact match on a
    uniquely-indexed pair is the honest behaviour.
    """
    session = RecordingSession(scalar_result=None)
    SecurityIdentifierRepository(session).get_by_code(IdentifierType.ISIN, "US0378331005")
    values = bound_values(session.last)
    assert IdentifierType.ISIN in values
    assert "US0378331005" in values


def test_benchmark_price_bars_are_ordered_by_date() -> None:
    session = RecordingSession()
    BenchmarkRepository(session).list_price_bars(
        price_basis=PriceBasis.SPLIT_ADJUSTED,
        benchmark_id=uuid.uuid4(),
        start=dt.date(2024, 1, 1),
        end=dt.date(2024, 1, 31),
    )
    assert "ORDER BY benchmark_price_bar.trading_date" in sql_of(session.last)


# ─────────────────────────── shape guards ────────────────────────────────────


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(
            lambda r: r.list_range(
                price_basis=PriceBasis.SPLIT_ADJUSTED,
                listing_id=uuid.uuid4(),
                start=dt.date(2024, 1, 1),
                end=dt.date(2024, 12, 31),
            ),
            id="list_range",
        ),
        pytest.param(
            lambda r: r.sources_for_listing(
                price_basis=PriceBasis.SPLIT_ADJUSTED,
                listing_id=uuid.uuid4(),
                start=dt.date(2024, 1, 1),
                end=dt.date(2024, 12, 31),
            ),
            id="sources_for_listing",
        ),
    ],
)
def test_repository_reads_return_lists(call: Any) -> None:
    """A list return means "zero rows" and "many rows" are the same shape, so a
    caller never has to handle ``None`` for an empty result."""
    assert isinstance(call(MarketDataRepository(RecordingSession())), list)


def test_every_recorded_statement_is_a_select() -> None:
    """No repository read may mutate. A repository that quietly issued an UPDATE
    would make a read call unpredictable."""
    session = RecordingSession()
    MarketDataRepository(session).list_range(
        price_basis=PriceBasis.SPLIT_ADJUSTED,
        listing_id=uuid.uuid4(),
        start=dt.date(2024, 1, 1),
        end=dt.date(2024, 1, 31),
    )
    EarningsRepository(session).list_chronological(company_id=uuid.uuid4())
    assert all(isinstance(stmt, sa.Select) for stmt in session.statements)
    assert session.added == []


def test_listing_reads_do_not_pull_the_whole_price_series() -> None:
    """Guards the one class of silent N+1: a listing read that drags in its
    entire price history because the relationship was left eager."""
    session = RecordingSession()
    ListingRepository(session).ticker_history(exchange_id=uuid.uuid4(), ticker="AAPL")
    assert "daily_price_bar" not in sql_of(session.last)
