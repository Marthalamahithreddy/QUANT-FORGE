"""Integration tests for the daily-bar writer, against real PostgreSQL.

The writer's contract is narrow and strict: write what is new, skip what is
identical, and **never** overwrite. Each of those three is tested against the real
``NUMERIC`` columns, because the interesting failures -- scale mismatches, partial
unique indexes, ``Decimal`` round-tripping -- are exactly the ones SQLite would
not reproduce.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_EVEN, Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from quantforge.ingestion.canonical import (
    CanonicalDailyBar,
    CanonicalEquityReference,
    ResolvedVenue,
)
from quantforge.ingestion.daily import (
    BarConflictError,
    DailyBarIngestor,
    IngestCounts,
    _quantise,
)
from quantforge.ingestion.reference import ReferenceResolver
from quantforge.ingestion.sources import get_or_create_data_source
from quantforge.models.enums import IngestionStatus
from quantforge.models.ingestion import IngestionRun
from quantforge.models.market import DailyPriceBar

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
SESSION = date(2026, 9, 1)
XNAS = ResolvedVenue(mic="XNAS", name="NasdaqGS", timezone_name="America/New_York")


@pytest.fixture
def source_id(db_session: Session) -> uuid.UUID:
    row = get_or_create_data_source(db_session, name="massive")
    db_session.commit()
    assert row.id is not None
    return row.id


@pytest.fixture
def listing_id(db_session: Session, source_id: uuid.UUID) -> uuid.UUID:
    """A resolved listing, so the writer has something real to attach bars to."""
    reference = CanonicalEquityReference(
        ticker="AAPL",
        security_type="EQUITY",
        venue=XNAS,
        company_name="Apple Inc.",
        cik="0000320193",
        share_class_figi="BBG001S5QY8",
        currency_code="USD",
    )
    result = ReferenceResolver(db_session).resolve(
        reference, source_id=source_id, as_of=date(2026, 9, 1)
    )
    db_session.commit()
    return result.listing_id


def _bar(**overrides: object) -> CanonicalDailyBar:
    fields: dict[str, object] = {
        "trading_session_date": SESSION,
        "open": Decimal("100.00"),
        "high": Decimal("105.00"),
        "low": Decimal("99.00"),
        "close": Decimal("104.00"),
        "volume": 1_000_000,
    }
    fields.update(overrides)
    return CanonicalDailyBar(**fields)  # type: ignore[arg-type]


def _ingest(
    session: Session,
    listing: uuid.UUID,
    source: uuid.UUID,
    *bars: CanonicalDailyBar,
    run: IngestionRun | None = None,
) -> IngestCounts:
    return DailyBarIngestor(session, now=NOW, run=run).ingest(
        bars, listing_id=listing, source_id=source, ticker="AAPL"
    )


def _stored(session: Session, listing: uuid.UUID, when: date = SESSION) -> DailyPriceBar:
    row = session.scalar(
        sa.select(DailyPriceBar).where(
            DailyPriceBar.listing_id == listing,
            DailyPriceBar.trading_date == when,
        )
    )
    assert row is not None, "expected a stored bar"
    return row


# ── writing ─────────────────────────────────────────────────────────────────


def test_a_new_bar_is_written(
    db_session: Session, listing_id: uuid.UUID, source_id: uuid.UUID
) -> None:
    counts = _ingest(db_session, listing_id, source_id, _bar())
    db_session.commit()

    assert (counts.received, counts.written, counts.skipped) == (1, 1, 0)
    row = _stored(db_session, listing_id)
    assert row.close == Decimal("104.000000")
    assert row.ingested_at is not None
    assert row.source_id == source_id
    assert row.listing_id == listing_id


def test_the_run_is_recorded_on_the_written_row(
    db_session: Session, listing_id: uuid.UUID, source_id: uuid.UUID
) -> None:
    """A bar must be traceable to the run that wrote it.

    This is the link that answers "why does this row exist" without archaeology.
    """
    run = IngestionRun(
        provider="massive",
        dataset="daily-bars",
        started_at=NOW,
        request_parameters={"ticker": "AAPL"},
        records_received=0,
        records_written=0,
        records_skipped=0,
        records_failed=0,
    )
    db_session.add(run)
    db_session.flush()

    _ingest(db_session, listing_id, source_id, _bar(), run=run)
    run.records_written = 1
    run.finalize(
        status=IngestionStatus.SUCCEEDED,
        completed_at=NOW,
        records_received=1,
        records_written=1,
        records_skipped=0,
        records_failed=0,
    )
    db_session.commit()

    assert _stored(db_session, listing_id).ingestion_run_id == run.id


def test_observed_at_is_null_when_the_provider_gave_none(
    db_session: Session, listing_id: uuid.UUID, source_id: uuid.UUID
) -> None:
    """Availability time must not be invented from the ingestion time.

    Backfilling it with the session's close, or with "now", is how a point-in-time
    backtest quietly acquires look-ahead bias.
    """
    _ingest(db_session, listing_id, source_id, _bar())
    db_session.commit()

    row = _stored(db_session, listing_id)
    assert row.observed_at is None
    assert row.ingested_at is not None, "ingestion time is a separate, real fact"


def test_adjusted_close_is_left_null(
    db_session: Session, listing_id: uuid.UUID, source_id: uuid.UUID
) -> None:
    """No adjustment is computed, so no adjusted series is implied to exist."""
    _ingest(db_session, listing_id, source_id, _bar())
    db_session.commit()
    assert _stored(db_session, listing_id).adjusted_close is None


def test_vwap_and_trades_are_stored(
    db_session: Session, listing_id: uuid.UUID, source_id: uuid.UUID
) -> None:
    _ingest(
        db_session,
        listing_id,
        source_id,
        _bar(vwap=Decimal("101.50"), trades=12_345),
    )
    db_session.commit()

    row = _stored(db_session, listing_id)
    assert row.vwap == Decimal("101.500000")
    assert row.trades == 12_345


def test_an_absent_vwap_stays_null_rather_than_becoming_zero(
    db_session: Session, listing_id: uuid.UUID, source_id: uuid.UUID
) -> None:
    """Zero would assert "no trades", which is a different and stronger claim."""
    _ingest(db_session, listing_id, source_id, _bar())
    db_session.commit()
    assert _stored(db_session, listing_id).vwap is None


def test_prices_come_back_as_exact_decimals(
    db_session: Session, listing_id: uuid.UUID, source_id: uuid.UUID
) -> None:
    _ingest(
        db_session,
        listing_id,
        source_id,
        _bar(
            open=Decimal("0.000001"),
            low=Decimal("0.000001"),
            high=Decimal("12345.678901"),
            close=Decimal("12345.678901"),
        ),
    )
    db_session.commit()

    row = _stored(db_session, listing_id)
    assert isinstance(row.close, Decimal)
    assert str(row.close) == "12345.678901", "exact digits, not a float artefact"


# ── idempotency ─────────────────────────────────────────────────────────────


def test_an_identical_bar_is_skipped_not_duplicated(
    db_session: Session, listing_id: uuid.UUID, source_id: uuid.UUID
) -> None:
    """Re-running an unchanged range must be a no-op.

    This is the property that makes a nightly refresh safe to schedule.
    """
    _ingest(db_session, listing_id, source_id, _bar())
    db_session.commit()

    counts = _ingest(db_session, listing_id, source_id, _bar())
    db_session.commit()

    assert (counts.written, counts.skipped) == (0, 1)
    assert db_session.query(DailyPriceBar).count() == 1


def test_several_identical_runs_still_write_once(
    db_session: Session, listing_id: uuid.UUID, source_id: uuid.UUID
) -> None:
    for _ in range(4):
        _ingest(db_session, listing_id, source_id, _bar())
        db_session.commit()
    assert db_session.query(DailyPriceBar).count() == 1


def test_sub_scale_precision_does_not_look_like_a_change(
    db_session: Session, listing_id: uuid.UUID, source_id: uuid.UUID
) -> None:
    """A difference below the column's scale is not a revision.

    The provider may send more digits than the column keeps. Comparing the full
    incoming value against the rounded stored one would report a conflict on every
    single run, which trains an operator to ignore conflicts.
    """
    _ingest(db_session, listing_id, source_id, _bar(close=Decimal("104.0000001")))
    db_session.commit()

    counts = _ingest(db_session, listing_id, source_id, _bar(close=Decimal("104.0000002")))
    db_session.commit()
    assert counts.skipped == 1
    assert str(_stored(db_session, listing_id).close) == "104.000000"


@pytest.mark.parametrize(
    ("incoming", "stored"),
    [
        (Decimal("1.0000005"), Decimal("1.000001")),
        (Decimal("1.0000015"), Decimal("1.000002")),
        (Decimal("2.0000025"), Decimal("2.000003")),
        (Decimal("-1.0000005"), Decimal("-1.000001")),
        (Decimal("123.4567895"), Decimal("123.456790")),
    ],
)
def test_quantisation_matches_postgres_rounding_of_ties(incoming: Decimal, stored: Decimal) -> None:
    """Regression: ``ROUND_HALF_EVEN`` does not match PostgreSQL's ties.

    Python's default half-even and PostgreSQL's ties-away-from-zero disagree on an
    exact tie at the seventh decimal. With half-even, Python would compare
    1.000000 against a column holding 1.000001 and report an unchanged series as a
    conflict -- forever, on every run.

    A unit test because the property is about the arithmetic; the integration tests
    around it prove the column agrees with it.
    """
    assert _quantise(incoming) == stored


@pytest.mark.parametrize("tie", [Decimal("1.0000005"), Decimal("2.0000025"), Decimal("-1.0000005")])
def test_half_even_really_would_have_been_wrong(tie: Decimal) -> None:
    """Guards the guard: the two modes must actually differ, or the fix is untested.

    On these ties half-even rounds down to an even last digit while PostgreSQL
    rounds away from zero, so the rejected alternative yields a different stored
    value -- and therefore a phantom conflict on every subsequent run.
    """
    half_even = tie.quantize(Decimal("0.000001"), rounding=ROUND_HALF_EVEN)
    assert half_even != _quantise(tie)


# ── conflicts ───────────────────────────────────────────────────────────────


def test_a_changed_price_is_refused_and_nothing_is_overwritten(
    db_session: Session, listing_id: uuid.UUID, source_id: uuid.UUID
) -> None:
    """A vendor revising history is a decision, not an automatic update.

    Silently restating would rewrite the past under any backtest already run, and
    the rewrite would be invisible.
    """
    _ingest(db_session, listing_id, source_id, _bar(close=Decimal("104.00")))
    db_session.commit()

    with pytest.raises(BarConflictError, match="stored bar differs"):
        _ingest(
            db_session, listing_id, source_id, _bar(close=Decimal("110.00"), high=Decimal("111.00"))
        )
    db_session.rollback()

    assert str(_stored(db_session, listing_id).close) == "104.000000"


def test_a_conflict_names_the_fields_that_differ(
    db_session: Session, listing_id: uuid.UUID, source_id: uuid.UUID
) -> None:
    """The operator needs to know *what* changed to judge whether it is a bug.

    Exactly two fields differ here, so this also pins the set down: an
    implementation that compared every column would report ``open``/``low``
    too, and one that compared only ``close`` would miss the volume.
    """
    _ingest(db_session, listing_id, source_id, _bar())
    db_session.commit()

    with pytest.raises(BarConflictError) as caught:
        _ingest(
            db_session,
            listing_id,
            source_id,
            _bar(close=Decimal("103.00"), volume=2_000_000),
        )
    db_session.rollback()

    assert set(caught.value.differences) == {"close", "volume"}
    assert caught.value.ticker == "AAPL"
    assert caught.value.trading_date == SESSION
    assert "nothing was overwritten" in str(caught.value).lower() or "left untouched" in str(
        caught.value
    )


def test_a_changed_volume_alone_is_a_conflict(
    db_session: Session, listing_id: uuid.UUID, source_id: uuid.UUID
) -> None:
    """Volume is part of the observation, so a revision is still a revision."""
    _ingest(db_session, listing_id, source_id, _bar(volume=1_000))
    db_session.commit()
    with pytest.raises(BarConflictError):
        _ingest(db_session, listing_id, source_id, _bar(volume=2_000))
    db_session.rollback()


def test_a_newly_supplied_optional_field_is_not_a_conflict(
    db_session: Session, listing_id: uuid.UUID, source_id: uuid.UUID
) -> None:
    """Absence is not a claim.

    A bar stored without a VWAP, then re-fetched with one, is the provider telling
    us more than it did before -- not the provider contradicting itself.
    """
    _ingest(db_session, listing_id, source_id, _bar())
    db_session.commit()

    counts = _ingest(db_session, listing_id, source_id, _bar(vwap=Decimal("101.50"), trades=99))
    db_session.commit()
    assert counts.skipped == 1


# ── counting ────────────────────────────────────────────────────────────────


def test_counts_partition_what_was_received(
    db_session: Session, listing_id: uuid.UUID, source_id: uuid.UUID
) -> None:
    """``written + skipped + failed == received``, or a bar went missing silently."""
    other = _bar(trading_session_date=date(2026, 9, 2))
    _ingest(db_session, listing_id, source_id, _bar(), other)
    counts = _ingest(db_session, listing_id, source_id, _bar(), other)
    db_session.commit()

    assert counts.received == 2
    assert counts.written + counts.skipped + counts.failed == counts.received
    assert counts.skipped == 2


def test_bars_in_any_order_produce_the_same_rows(
    db_session: Session, listing_id: uuid.UUID, source_id: uuid.UUID
) -> None:
    """Order must not matter, so a provider reordering its results is harmless."""
    first = _bar(trading_session_date=date(2026, 9, 1))
    second = _bar(trading_session_date=date(2026, 9, 2), close=Decimal("105.00"))

    _ingest(db_session, listing_id, source_id, second, first)
    db_session.commit()

    assert db_session.query(DailyPriceBar).count() == 2
    counts = _ingest(db_session, listing_id, source_id, first, second)
    db_session.commit()
    assert counts.skipped == 2


def test_the_same_session_twice_in_one_batch_is_a_conflict(
    db_session: Session, listing_id: uuid.UUID, source_id: uuid.UUID
) -> None:
    """A provider page that repeats a date is a real inconsistency, not a skip."""
    with pytest.raises(BarConflictError):
        _ingest(
            db_session,
            listing_id,
            source_id,
            _bar(close=Decimal("104.00")),
            _bar(close=Decimal("110.00"), high=Decimal("111.00")),
        )
    db_session.rollback()


# ── the same session on two listings ────────────────────────────────────────


def test_two_listings_may_hold_the_same_session(db_session: Session, source_id: uuid.UUID) -> None:
    """The same symbol on two venues has two independent price series.

    Keying on the date alone would make the second venue's bars collide with the
    first's and be reported as a revision of it.
    """
    listings: list[uuid.UUID] = []
    for mic, name, ticker in (("XNAS", "NasdaqGS", "AAPL"), ("XNYS", "NYSE", "AAPL")):
        reference = CanonicalEquityReference(
            ticker=ticker,
            security_type="EQUITY",
            venue=ResolvedVenue(mic=mic, name=name, timezone_name="America/New_York"),
            company_name="Apple Inc.",
            cik="0000320193",
            share_class_figi="BBG001S5QY8",
        )
        result = ReferenceResolver(db_session).resolve(
            reference, source_id=source_id, as_of=date(2026, 9, 1)
        )
        listings.append(result.listing_id)
    db_session.commit()

    _ingest(db_session, listings[0], source_id, _bar(close=Decimal("104.00")))
    db_session.commit()
    _ingest(db_session, listings[1], source_id, _bar(close=Decimal("103.50")))
    db_session.commit()

    assert db_session.query(DailyPriceBar).count() == 2
    assert str(_stored(db_session, listings[0]).close) == "104.000000"
    assert str(_stored(db_session, listings[1]).close) == "103.500000"
