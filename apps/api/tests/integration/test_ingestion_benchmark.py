"""Integration tests for benchmark identity resolution and level ingestion.

Mirrors ``tests/integration/test_ingestion_daily.py``'s structure and rigor,
scoped to what is actually different for
:class:`~quantforge.models.benchmark.BenchmarkPriceBar`: a single comparable
field (``close``, not five) and a deterministic benchmark get-or-create step
that has no equivalent in the stock path (a `Listing` is resolved through
reference ingestion; a `Benchmark` is resolved here, explicitly, by name and
symbol).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from quantforge.ingestion.benchmark import (
    BenchmarkBarConflictError,
    BenchmarkBarIngestor,
    BenchmarkIdentityConflict,
    get_or_create_benchmark,
)
from quantforge.ingestion.canonical import CanonicalDailyBar
from quantforge.ingestion.daily import IngestCounts
from quantforge.ingestion.sources import get_or_create_data_source
from quantforge.models.benchmark import Benchmark, BenchmarkPriceBar
from quantforge.models.enums import IngestionStatus
from quantforge.models.ingestion import IngestionRun

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
SESSION = date(2026, 9, 1)
SPY_NAME = "SPY (ETF proxy for the S&P 500)"


@pytest.fixture
def source_id(db_session: Session) -> uuid.UUID:
    row = get_or_create_data_source(db_session, name="massive")
    db_session.commit()
    assert row.id is not None
    return row.id


@pytest.fixture
def benchmark_id(db_session: Session, source_id: uuid.UUID) -> uuid.UUID:
    row = get_or_create_benchmark(
        db_session, name=SPY_NAME, symbol="SPY", currency="USD", source_id=source_id
    )
    db_session.commit()
    return row.id


def _bar(**overrides: object) -> CanonicalDailyBar:
    """A coherent canonical bar. Only ``close``/``trading_session_date``/
    ``observed_at`` are actually read by the benchmark writer -- the rest
    exist because :class:`CanonicalDailyBar` requires OHLC coherence
    regardless of what the caller plans to use.

    ``low``/``high`` default to a band around ``close`` (rather than a fixed
    680/679/690 band) so a caller overriding only ``close`` to exercise a
    conflict or a different level never has to separately widen the session
    range to keep the bar internally consistent.
    """
    close = overrides.get("close", Decimal("685.00"))
    assert isinstance(close, Decimal)
    fields: dict[str, object] = {
        "trading_session_date": SESSION,
        "open": close,
        "high": close + Decimal("5.00"),
        "low": close - Decimal("5.00"),
        "close": close,
        "volume": 70_000_000,
    }
    fields.update(overrides)
    return CanonicalDailyBar(**fields)  # type: ignore[arg-type]


def _ingest(
    session: Session,
    benchmark: uuid.UUID,
    source: uuid.UUID,
    *bars: CanonicalDailyBar,
    run: IngestionRun | None = None,
) -> IngestCounts:
    return BenchmarkBarIngestor(session, now=NOW).ingest(
        bars, benchmark_id=benchmark, source_id=source, symbol="SPY", run=run
    )


def _stored(session: Session, benchmark: uuid.UUID, when: date = SESSION) -> BenchmarkPriceBar:
    row = session.scalar(
        sa.select(BenchmarkPriceBar).where(
            BenchmarkPriceBar.benchmark_id == benchmark,
            BenchmarkPriceBar.trading_date == when,
        )
    )
    assert row is not None, "expected a stored benchmark level"
    return row


# ── get_or_create_benchmark ──────────────────────────────────────────────────


def test_a_new_benchmark_is_created(db_session: Session) -> None:
    created = get_or_create_benchmark(db_session, name=SPY_NAME, symbol="SPY", currency="USD")
    db_session.commit()

    assert created.id is not None
    assert created.name == SPY_NAME
    assert created.symbol == "SPY"
    assert created.currency == "USD"


def test_a_matching_symbol_and_name_resolves_the_same_row(db_session: Session) -> None:
    first = get_or_create_benchmark(db_session, name=SPY_NAME, symbol="SPY")
    db_session.commit()

    second = get_or_create_benchmark(db_session, name=SPY_NAME, symbol="SPY")
    db_session.commit()

    assert second.id == first.id
    assert db_session.query(Benchmark).count() == 1


def test_an_incompatible_existing_name_is_a_conflict_not_a_rename(db_session: Session) -> None:
    get_or_create_benchmark(db_session, name=SPY_NAME, symbol="SPY")
    db_session.commit()

    with pytest.raises(BenchmarkIdentityConflict, match="SPY"):
        get_or_create_benchmark(db_session, name="A Completely Different Index", symbol="SPY")

    assert db_session.query(Benchmark).count() == 1
    assert db_session.query(Benchmark).one().name == SPY_NAME


def test_an_ambiguous_symbol_is_a_conflict(db_session: Session) -> None:
    """The schema allows two rows to share a symbol; get-or-create must not guess."""
    db_session.add_all(
        [
            Benchmark(name="SPY (ETF proxy for the S&P 500)", symbol="SPY"),
            Benchmark(name="SPY Volatility Proxy (unrelated)", symbol="SPY"),
        ]
    )
    db_session.commit()

    with pytest.raises(BenchmarkIdentityConflict, match="2 existing"):
        get_or_create_benchmark(db_session, name=SPY_NAME, symbol="SPY")


def test_different_symbols_are_different_benchmarks(db_session: Session) -> None:
    spy = get_or_create_benchmark(db_session, name=SPY_NAME, symbol="SPY")
    qqq = get_or_create_benchmark(
        db_session, name="QQQ (ETF proxy for the Nasdaq-100)", symbol="QQQ"
    )
    db_session.commit()

    assert spy.id != qqq.id
    assert db_session.query(Benchmark).count() == 2


# ── writing ─────────────────────────────────────────────────────────────────


def test_a_new_level_is_written(
    db_session: Session, benchmark_id: uuid.UUID, source_id: uuid.UUID
) -> None:
    counts = _ingest(db_session, benchmark_id, source_id, _bar())
    db_session.commit()

    assert (counts.received, counts.written, counts.skipped) == (1, 1, 0)
    row = _stored(db_session, benchmark_id)
    assert row.close == Decimal("685.000000")
    assert row.ingested_at is not None
    assert row.source_id == source_id
    assert row.benchmark_id == benchmark_id


def test_observed_at_is_null_when_the_provider_gave_none(
    db_session: Session, benchmark_id: uuid.UUID, source_id: uuid.UUID
) -> None:
    _ingest(db_session, benchmark_id, source_id, _bar())
    db_session.commit()

    row = _stored(db_session, benchmark_id)
    assert row.observed_at is None
    assert row.ingested_at is not None


def test_close_comes_back_as_an_exact_decimal(
    db_session: Session, benchmark_id: uuid.UUID, source_id: uuid.UUID
) -> None:
    _ingest(
        db_session,
        benchmark_id,
        source_id,
        _bar(
            open=Decimal("0.000001"),
            low=Decimal("0.000001"),
            high=Decimal("12345.678901"),
            close=Decimal("12345.678901"),
        ),
    )
    db_session.commit()

    row = _stored(db_session, benchmark_id)
    assert isinstance(row.close, Decimal)
    assert str(row.close) == "12345.678901"


# ── idempotency ─────────────────────────────────────────────────────────────


def test_an_identical_level_is_skipped_not_duplicated(
    db_session: Session, benchmark_id: uuid.UUID, source_id: uuid.UUID
) -> None:
    _ingest(db_session, benchmark_id, source_id, _bar())
    db_session.commit()

    counts = _ingest(db_session, benchmark_id, source_id, _bar())
    db_session.commit()

    assert (counts.written, counts.skipped) == (0, 1)
    assert db_session.query(BenchmarkPriceBar).count() == 1


def test_several_identical_runs_still_write_once(
    db_session: Session, benchmark_id: uuid.UUID, source_id: uuid.UUID
) -> None:
    for _ in range(4):
        _ingest(db_session, benchmark_id, source_id, _bar())
        db_session.commit()
    assert db_session.query(BenchmarkPriceBar).count() == 1


def test_sub_scale_precision_does_not_look_like_a_change(
    db_session: Session, benchmark_id: uuid.UUID, source_id: uuid.UUID
) -> None:
    _ingest(db_session, benchmark_id, source_id, _bar(close=Decimal("685.0000001")))
    db_session.commit()

    counts = _ingest(db_session, benchmark_id, source_id, _bar(close=Decimal("685.0000002")))
    db_session.commit()
    assert counts.skipped == 1
    assert str(_stored(db_session, benchmark_id).close) == "685.000000"


# ── conflicts ───────────────────────────────────────────────────────────────


def test_a_changed_close_is_refused_and_nothing_is_overwritten(
    db_session: Session, benchmark_id: uuid.UUID, source_id: uuid.UUID
) -> None:
    _ingest(db_session, benchmark_id, source_id, _bar(close=Decimal("685.00")))
    db_session.commit()

    with pytest.raises(BenchmarkBarConflictError, match="stored benchmark level differs"):
        _ingest(db_session, benchmark_id, source_id, _bar(close=Decimal("690.00")))
    db_session.rollback()

    assert str(_stored(db_session, benchmark_id).close) == "685.000000"


def test_a_conflict_names_the_field_and_carries_context(
    db_session: Session, benchmark_id: uuid.UUID, source_id: uuid.UUID
) -> None:
    _ingest(db_session, benchmark_id, source_id, _bar())
    db_session.commit()

    with pytest.raises(BenchmarkBarConflictError) as caught:
        _ingest(db_session, benchmark_id, source_id, _bar(close=Decimal("700.00")))
    db_session.rollback()

    assert set(caught.value.differences) == {"close"}
    assert caught.value.symbol == "SPY"
    assert caught.value.trading_date == SESSION
    assert "left untouched" in str(caught.value)


# ── source separation ────────────────────────────────────────────────────────


def test_two_sources_may_hold_the_same_session(
    db_session: Session, benchmark_id: uuid.UUID, source_id: uuid.UUID
) -> None:
    """Two vendors disagreeing about the same level both survive as separate rows."""
    other_source = get_or_create_data_source(db_session, name="alt-benchmark-vendor")
    db_session.commit()

    _ingest(db_session, benchmark_id, source_id, _bar(close=Decimal("685.00")))
    _ingest(db_session, benchmark_id, other_source.id, _bar(close=Decimal("686.00")))
    db_session.commit()

    assert db_session.query(BenchmarkPriceBar).count() == 2


def test_two_benchmarks_may_hold_the_same_session_and_source(
    db_session: Session, source_id: uuid.UUID
) -> None:
    spy = get_or_create_benchmark(db_session, name=SPY_NAME, symbol="SPY")
    qqq = get_or_create_benchmark(
        db_session, name="QQQ (ETF proxy for the Nasdaq-100)", symbol="QQQ"
    )
    db_session.commit()

    _ingest(db_session, spy.id, source_id, _bar(close=Decimal("685.00")))
    _ingest(db_session, qqq.id, source_id, _bar(close=Decimal("610.00")))
    db_session.commit()

    assert db_session.query(BenchmarkPriceBar).count() == 2


# ── run linkage ──────────────────────────────────────────────────────────────


def test_counts_are_applied_to_the_run(
    db_session: Session, benchmark_id: uuid.UUID, source_id: uuid.UUID
) -> None:
    run = IngestionRun(
        provider="massive",
        dataset="benchmark-daily-bars",
        started_at=NOW,
        request_parameters={"symbol": "SPY"},
        records_received=0,
        records_written=0,
        records_skipped=0,
        records_failed=0,
    )
    db_session.add(run)
    db_session.flush()

    _ingest(db_session, benchmark_id, source_id, _bar(), run=run)
    run.finalize(
        status=IngestionStatus.SUCCEEDED,
        completed_at=NOW,
        records_received=1,
        records_written=1,
        records_skipped=0,
        records_failed=0,
    )
    db_session.commit()

    assert run.records_written == 1
    assert run.status is IngestionStatus.SUCCEEDED
