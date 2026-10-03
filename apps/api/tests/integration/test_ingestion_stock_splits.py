"""Stock-split ingestion, identity resolution, coverage and the guard, against real PostgreSQL.

Uses a stub provider (no Massive access). Covers: historical ticker resolution on the
execution date (never the current ticker), the half-open listing boundaries, ambiguity,
idempotency and conflicts, raw provenance, the Step 10.4H failed-run guarantees, and --
the point of the slice -- that an empty table never reads as "no split" without coverage.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import httpx2 as httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from quantforge.ingestion.canonical import CanonicalStockSplit
from quantforge.ingestion.raw_store import RawResponseStore
from quantforge.ingestion.sources import get_or_create_data_source
from quantforge.ingestion.splits import (
    COVERAGE_SAFETY_MARGIN_DAYS,
    AmbiguousSplitSecurityError,
    IncompleteSplitFetchError,
    InvalidSplitRangeError,
    RetiredTickerCoverageError,
    ReusedTickerRangeError,
    SplitIngestionService,
    StockSplitConflictError,
    UnknownSplitTickerError,
    UnmappedSplitError,
)
from quantforge.market import GuardStatus
from quantforge.models.company import Company
from quantforge.models.corporate_action import StockSplit, StockSplitCoverage
from quantforge.models.enums import (
    IngestionStatus,
    PriceBasis,
    ProviderType,
    SecurityType,
    StockSplitAdjustmentType,
)
from quantforge.models.ingestion import IngestionRun
from quantforge.models.market import DailyPriceBar
from quantforge.models.provenance import DataSource
from quantforge.models.security import Exchange, Listing, Security
from quantforge.providers.base import RawCapture
from quantforge.providers.errors import ProviderServerError
from quantforge.providers.massive.client import MassiveClient, MassiveConfig
from quantforge.providers.splits import SplitsResult
from quantforge.repositories.stock_split import StockSplitRepository

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
START, END = date(2024, 1, 1), date(2025, 12, 31)


def _split(**overrides: object) -> CanonicalStockSplit:
    fields: dict[str, object] = {
        "provider_split_id": "E-nvda-2024",
        "ticker": "NVDA",
        "execution_date": date(2024, 6, 10),
        "old_shares": Decimal(1),
        "new_shares": Decimal(10),
        "adjustment_type": StockSplitAdjustmentType.FORWARD_SPLIT,
    }
    fields.update(overrides)
    return CanonicalStockSplit(**fields)  # type: ignore[arg-type]


class StubProvider:
    name = "massive"

    def __init__(
        self,
        splits: list[CanonicalStockSplit],
        *,
        rejected: tuple[str, ...] = (),
        error: Exception | None = None,
        body: bytes = b'{"status":"OK","results":[]}',
    ) -> None:
        self._result = SplitsResult(
            splits=splits,
            rejected=rejected,
            captures=(
                RawCapture(
                    provider="massive",
                    dataset="stock-splits",
                    endpoint="/stocks/v1/splits",
                    request_parameters={"ticker": "NVDA"},
                    body=body,
                    retrieved_at=NOW,
                ),
            ),
        )
        self._error = error

    async def get_stock_splits(self, ticker: str) -> SplitsResult:
        del ticker
        if self._error is not None:
            raise self._error
        return self._result


@pytest.fixture
def exchange(db_session: Session) -> Exchange:
    row = Exchange(mic="XNAS", name="Nasdaq test", timezone="America/New_York")
    db_session.add(row)
    db_session.commit()
    return row


def _security(session: Session, name: str) -> Security:
    company = Company(legal_name=f"{name} Inc.", display_name=name)
    session.add(company)
    session.flush()
    security = Security(
        company_id=company.id, security_type=SecurityType.EQUITY, name=name, currency="USD"
    )
    session.add(security)
    session.flush()
    return security


def _listing(
    session: Session,
    security: Security,
    exchange: Exchange,
    ticker: str,
    valid_from: date,
    valid_to: date | None = None,
    *,
    primary: bool | None = None,
) -> Listing:
    row = Listing(
        security_id=security.id,
        exchange_id=exchange.id,
        ticker=ticker,
        valid_from=valid_from,
        valid_to=valid_to,
        is_primary=(valid_to is None) if primary is None else primary,
    )
    session.add(row)
    session.flush()
    return row


@pytest.fixture
def nvda(db_session: Session, exchange: Exchange) -> Security:
    security = _security(db_session, "Nvidia")
    _listing(db_session, security, exchange, "NVDA", date(2000, 1, 3))
    db_session.commit()
    return security


def _service(session: Session, provider: StubProvider, tmp_path: Path) -> SplitIngestionService:
    return SplitIngestionService(
        session=session,
        provider=provider,
        store=RawResponseStore(root=tmp_path / "raw"),
        now=NOW,
    )


async def _ingest(
    session: Session,
    provider: StubProvider,
    tmp_path: Path,
    *,
    ticker: str = "NVDA",
    start: date = START,
    end: date = END,
) -> object:
    outcome = await _service(session, provider, tmp_path).ingest_stock_splits(
        ticker, start, end, provider_history_start=None
    )
    session.commit()
    return outcome


# ── persistence and provenance ──────────────────────────────────────────────


@pytest.mark.anyio
async def test_a_split_is_stored_against_the_security_with_provenance(
    nvda: Security, db_session: Session, tmp_path: Path
) -> None:
    await _ingest(db_session, StubProvider([_split()]), tmp_path)

    row = db_session.query(StockSplit).one()
    assert row.security_id == nvda.id
    assert (row.old_shares, row.new_shares) == (Decimal(1), Decimal(10))
    assert row.execution_date == date(2024, 6, 10)
    assert row.provider_ticker == "NVDA" and row.provider_split_id == "E-nvda-2024"
    assert row.adjustment_type is StockSplitAdjustmentType.FORWARD_SPLIT
    assert row.ingested_at == NOW
    run = db_session.query(IngestionRun).one()
    assert row.ingestion_run_id == run.id
    assert (run.provider, run.dataset, run.status) == (
        "massive",
        "stock-splits",
        IngestionStatus.SUCCEEDED,
    )
    assert (run.records_received, run.records_written, run.records_skipped) == (1, 1, 0)
    assert db_session.query(DataSource).one().id == row.source_id


@pytest.mark.anyio
async def test_there_is_no_availability_column_to_misuse() -> None:
    columns = {c.name for c in StockSplit.__table__.columns}
    assert "observed_at" not in columns and "observed_at_basis" not in columns


@pytest.mark.anyio
async def test_decimal_ratios_round_trip_exactly(
    nvda: Security, db_session: Session, tmp_path: Path
) -> None:
    split = _split(
        provider_split_id="E-frac",
        old_shares=Decimal("1"),
        new_shares=Decimal("1.027"),
        adjustment_type=StockSplitAdjustmentType.STOCK_DIVIDEND,
    )
    await _ingest(db_session, StubProvider([split]), tmp_path)

    stored = db_session.query(StockSplit).one()
    assert str(stored.new_shares) == "1.027"


@pytest.mark.anyio
async def test_a_reverse_split_is_stored_as_given(
    nvda: Security, db_session: Session, tmp_path: Path
) -> None:
    split = _split(
        old_shares=Decimal(10),
        new_shares=Decimal(1),
        adjustment_type=StockSplitAdjustmentType.REVERSE_SPLIT,
    )
    await _ingest(db_session, StubProvider([split]), tmp_path)

    stored = db_session.query(StockSplit).one()
    assert (stored.old_shares, stored.new_shares) == (Decimal(10), Decimal(1))


@pytest.mark.anyio
async def test_raw_response_is_preserved_with_a_hash_and_no_key(
    nvda: Security, db_session: Session, tmp_path: Path
) -> None:
    body = b'{"status":"OK","results":[{"id":"E-nvda-2024"}]}'
    await _ingest(db_session, StubProvider([_split()], body=body), tmp_path)

    run = db_session.query(IngestionRun).one()
    assert run.raw_artifact_path is not None and run.raw_content_sha256 is not None
    raw_root = tmp_path / "raw"
    stored = [p for p in raw_root.rglob("*") if p.is_file()]
    assert any("stock-splits" in str(p) for p in stored)
    assert any(p.read_bytes() == body for p in stored)
    manifest_text = " ".join(p.read_text(errors="ignore") for p in stored if p.suffix == ".json")
    assert "apiKey" not in manifest_text and "Bearer" not in manifest_text


# ── idempotency, conflicts, isolation ───────────────────────────────────────


@pytest.mark.anyio
async def test_an_identical_rerun_is_a_skip(
    nvda: Security, db_session: Session, tmp_path: Path
) -> None:
    await _ingest(db_session, StubProvider([_split()]), tmp_path)
    await _ingest(db_session, StubProvider([_split()]), tmp_path)

    assert db_session.query(StockSplit).count() == 1
    assert db_session.query(StockSplitCoverage).count() == 1, "coverage is idempotent too"
    second = db_session.query(IngestionRun).order_by(IngestionRun.started_at.desc()).first()
    assert second is not None and db_session.query(IngestionRun).count() == 2


@pytest.mark.anyio
async def test_the_same_id_with_different_facts_is_a_conflict_and_changes_nothing(
    nvda: Security, db_session: Session, tmp_path: Path
) -> None:
    await _ingest(db_session, StubProvider([_split()]), tmp_path)

    changed = _split(new_shares=Decimal(20))
    with pytest.raises(StockSplitConflictError, match="new_shares"):
        await _ingest(db_session, StubProvider([changed]), tmp_path)
    db_session.rollback()

    assert db_session.query(StockSplit).one().new_shares == Decimal(10)


@pytest.mark.anyio
async def test_a_changed_execution_date_is_a_conflict(
    nvda: Security, db_session: Session, tmp_path: Path
) -> None:
    await _ingest(db_session, StubProvider([_split()]), tmp_path)

    with pytest.raises(StockSplitConflictError, match="execution_date"):
        await _ingest(
            db_session, StubProvider([_split(execution_date=date(2024, 6, 11))]), tmp_path
        )


@pytest.mark.anyio
async def test_the_same_provider_id_from_a_different_source_is_a_separate_row(
    nvda: Security, db_session: Session, tmp_path: Path
) -> None:
    await _ingest(db_session, StubProvider([_split()]), tmp_path)
    other = DataSource(name="other-vendor", provider_type=ProviderType.MARKET_DATA_VENDOR)
    db_session.add(other)
    db_session.flush()
    db_session.add(
        StockSplit(
            security_id=nvda.id,
            source_id=other.id,
            provider_split_id="E-nvda-2024",
            provider_ticker="NVDA",
            execution_date=date(2024, 6, 10),
            old_shares=Decimal(1),
            new_shares=Decimal(10),
            adjustment_type=StockSplitAdjustmentType.FORWARD_SPLIT,
        )
    )
    db_session.commit()

    assert db_session.query(StockSplit).count() == 2


@pytest.mark.anyio
async def test_records_outside_the_requested_range_are_skipped_not_stored(
    nvda: Security, db_session: Session, tmp_path: Path
) -> None:
    old = _split(provider_split_id="E-old", execution_date=date(2021, 7, 20))
    await _ingest(db_session, StubProvider([old, _split()]), tmp_path)

    assert [r.provider_split_id for r in db_session.query(StockSplit)] == ["E-nvda-2024"]
    run = db_session.query(IngestionRun).one()
    assert (run.records_received, run.records_written, run.records_skipped) == (2, 1, 1)


# ── historical ticker resolution ────────────────────────────────────────────


@pytest.mark.anyio
async def test_a_split_resolves_through_the_listing_valid_on_its_date(
    db_session: Session, exchange: Exchange, tmp_path: Path
) -> None:
    security = _security(db_session, "Renamer")
    _listing(db_session, security, exchange, "OLDN", date(2010, 1, 1), date(2024, 3, 1))
    _listing(db_session, security, exchange, "NEWN", date(2024, 3, 1))
    db_session.commit()

    split = _split(ticker="NEWN", provider_split_id="E-new", execution_date=date(2024, 4, 1))
    await _ingest(
        db_session,
        StubProvider([split]),
        tmp_path,
        ticker="NEWN",
        start=date(2024, 1, 1),
        end=date(2024, 6, 1),
    )

    assert db_session.query(StockSplit).one().security_id == security.id


@pytest.mark.anyio
async def test_coverage_through_a_retired_ticker_is_refused_before_any_run_opens(
    db_session: Session, exchange: Exchange, tmp_path: Path
) -> None:
    security = _security(db_session, "Renamer")
    _listing(db_session, security, exchange, "OLDN", date(2010, 1, 1), date(2024, 3, 1))
    _listing(db_session, security, exchange, "NEWN", date(2024, 3, 1))
    db_session.commit()

    service = _service(db_session, StubProvider([]), tmp_path)
    with pytest.raises(RetiredTickerCoverageError, match="its current ticker is NEWN"):
        await service.ingest_stock_splits(
            "OLDN", date(2023, 1, 1), date(2024, 2, 1), provider_history_start=None
        )

    assert db_session.query(IngestionRun).count() == 0
    assert db_session.query(StockSplitCoverage).count() == 0
    # An empty answer for the old symbol therefore can never make the old era look clear.
    assert _guard(db_session, security, date(2024, 1, 10), date(2024, 1, 20)).status is (
        GuardStatus.COVERAGE_UNAVAILABLE
    )


@pytest.mark.anyio
async def test_a_split_inside_the_range_but_before_the_rename_fails_the_run_and_covers_nothing(
    db_session: Session, exchange: Exchange, tmp_path: Path
) -> None:
    security = _security(db_session, "Renamer")
    _listing(db_session, security, exchange, "OLDN", date(2010, 1, 1), date(2024, 3, 1))
    _listing(db_session, security, exchange, "NEWN", date(2024, 3, 1))
    db_session.commit()

    early = _split(ticker="NEWN", provider_split_id="E-early", execution_date=date(2024, 2, 1))
    service = _service(db_session, StubProvider([early]), tmp_path)
    with pytest.raises(UnmappedSplitError, match="no coverage is recorded"):
        await service.ingest_stock_splits(
            "NEWN", date(2024, 1, 1), date(2024, 6, 1), provider_history_start=None
        )
    db_session.commit()

    assert db_session.query(StockSplit).count() == 0
    assert db_session.query(StockSplitCoverage).count() == 0
    run = db_session.query(IngestionRun).one()
    assert run.status is IngestionStatus.FAILED
    assert (run.records_received, run.records_failed, run.records_written) == (1, 1, 0)
    assert _guard(db_session, security, date(2024, 1, 30), date(2024, 2, 5)).status is (
        GuardStatus.COVERAGE_UNAVAILABLE
    )


@pytest.mark.anyio
async def test_narrowing_the_range_to_the_ticker_era_makes_the_same_record_harmless(
    db_session: Session, exchange: Exchange, tmp_path: Path
) -> None:
    security = _security(db_session, "Renamer")
    _listing(db_session, security, exchange, "OLDN", date(2010, 1, 1), date(2024, 3, 1))
    _listing(db_session, security, exchange, "NEWN", date(2024, 3, 1))
    db_session.commit()

    early = _split(ticker="NEWN", provider_split_id="E-early", execution_date=date(2024, 2, 1))
    await _ingest(
        db_session,
        StubProvider([early]),
        tmp_path,
        ticker="NEWN",
        start=date(2024, 3, 1),
        end=date(2024, 6, 1),
    )

    # Outside the REQUESTED range: skipped (not stored, not claimed).
    assert db_session.query(StockSplit).count() == 0
    (coverage,) = db_session.query(StockSplitCoverage).all()
    assert (coverage.covered_from, coverage.covered_to) == (date(2024, 3, 1), date(2024, 6, 1))
    assert _guard(db_session, security, date(2024, 1, 30), date(2024, 2, 5)).status is (
        GuardStatus.COVERAGE_UNAVAILABLE
    )


@pytest.mark.anyio
async def test_the_listing_start_boundary_is_inclusive(
    db_session: Session, exchange: Exchange, tmp_path: Path
) -> None:
    security = _security(db_session, "Edge")
    _listing(db_session, security, exchange, "EDGE", date(2024, 3, 1))
    db_session.commit()

    on_start = _split(ticker="EDGE", provider_split_id="E-on", execution_date=date(2024, 3, 1))
    await _ingest(
        db_session,
        StubProvider([on_start]),
        tmp_path,
        ticker="EDGE",
        start=date(2024, 3, 1),
        end=date(2024, 6, 1),
    )

    assert db_session.query(StockSplit).one().execution_date == date(2024, 3, 1)


@pytest.mark.anyio
async def test_a_split_the_day_before_the_listing_starts_is_unmapped_when_in_range(
    db_session: Session, exchange: Exchange, tmp_path: Path
) -> None:
    security = _security(db_session, "Edge")
    _listing(db_session, security, exchange, "EDGE", date(2024, 3, 1))
    db_session.commit()

    day_before = _split(ticker="EDGE", provider_split_id="E-b", execution_date=date(2024, 2, 29))
    with pytest.raises(UnmappedSplitError):
        await _ingest(
            db_session,
            StubProvider([day_before]),
            tmp_path,
            ticker="EDGE",
            start=date(2024, 1, 1),
            end=date(2024, 6, 1),
        )


@pytest.mark.anyio
async def test_contiguous_listings_meet_at_the_exclusive_end_with_no_gap_and_no_overlap(
    db_session: Session, exchange: Exchange, tmp_path: Path
) -> None:
    security = _security(db_session, "Mover")
    other = Exchange(mic="ARCX", name="Arca test", timezone="America/New_York")
    db_session.add(other)
    db_session.flush()
    _listing(db_session, security, exchange, "MOVE", date(2010, 1, 1), date(2024, 3, 1))
    _listing(db_session, security, other, "MOVE", date(2024, 3, 1))
    db_session.commit()

    handover = _split(ticker="MOVE", provider_split_id="E-hand", execution_date=date(2024, 3, 1))
    await _ingest(
        db_session,
        StubProvider([handover]),
        tmp_path,
        ticker="MOVE",
        start=date(2024, 1, 1),
        end=date(2024, 6, 1),
    )

    assert db_session.query(StockSplit).count() == 1
    (coverage,) = db_session.query(StockSplitCoverage).all()
    assert (coverage.covered_from, coverage.covered_to) == (date(2024, 1, 1), date(2024, 6, 1))


@pytest.mark.anyio
async def test_a_gap_between_listings_is_not_covered_and_a_split_in_it_is_unmapped(
    db_session: Session, exchange: Exchange, tmp_path: Path
) -> None:
    security = _security(db_session, "Gapper")
    other = Exchange(mic="ARCX", name="Arca test", timezone="America/New_York")
    db_session.add(other)
    db_session.flush()
    # valid_to is exclusive: the first era's last day is 2024-02-29.
    _listing(db_session, security, exchange, "GAPP", date(2010, 1, 1), date(2024, 3, 1))
    _listing(db_session, security, other, "GAPP", date(2024, 3, 5))
    db_session.commit()

    await _ingest(
        db_session,
        StubProvider([]),
        tmp_path,
        ticker="GAPP",
        start=date(2024, 1, 1),
        end=date(2024, 6, 1),
    )
    ranges = sorted((c.covered_from, c.covered_to) for c in db_session.query(StockSplitCoverage))
    assert ranges == [(date(2024, 1, 1), date(2024, 2, 29)), (date(2024, 3, 5), date(2024, 6, 1))]
    assert _guard(db_session, security, date(2024, 3, 2), date(2024, 3, 4)).status is (
        GuardStatus.COVERAGE_UNAVAILABLE
    )

    inside_gap = _split(ticker="GAPP", provider_split_id="E-gap", execution_date=date(2024, 3, 3))
    with pytest.raises(UnmappedSplitError):
        await _ingest(
            db_session,
            StubProvider([inside_gap]),
            tmp_path,
            ticker="GAPP",
            start=date(2024, 1, 1),
            end=date(2024, 6, 2),
        )


@pytest.mark.anyio
async def test_a_delisted_security_cannot_get_coverage(
    db_session: Session, exchange: Exchange, tmp_path: Path
) -> None:
    security = _security(db_session, "Gone")
    _listing(db_session, security, exchange, "GONE", date(2010, 1, 1), date(2024, 3, 1))
    db_session.commit()

    service = _service(db_session, StubProvider([]), tmp_path)
    with pytest.raises(RetiredTickerCoverageError, match="it has no current listing"):
        await service.ingest_stock_splits(
            "GONE", date(2024, 1, 1), date(2024, 2, 1), provider_history_start=None
        )
    assert db_session.query(IngestionRun).count() == 0


@pytest.mark.anyio
async def test_a_ticker_with_no_historical_listing_is_an_explicit_failure(
    db_session: Session, tmp_path: Path
) -> None:
    with pytest.raises(UnknownSplitTickerError, match="no stored evidence"):
        await _ingest(db_session, StubProvider([_split()]), tmp_path)
    db_session.rollback()
    assert db_session.query(StockSplit).count() == 0
    assert db_session.query(IngestionRun).count() == 0, "a usage error opens no run"


@pytest.mark.anyio
async def test_a_reused_ticker_over_the_range_is_ambiguous_and_nothing_is_guessed(
    db_session: Session, exchange: Exchange, tmp_path: Path
) -> None:
    first = _security(db_session, "First Holder")
    second = _security(db_session, "Second Holder")
    _listing(db_session, first, exchange, "NVDA", date(2000, 1, 3), date(2024, 5, 1))
    _listing(db_session, second, exchange, "NVDA", date(2024, 5, 1))
    db_session.commit()

    with pytest.raises(ReusedTickerRangeError, match="different securities") as raised:
        await _ingest(db_session, StubProvider([_split()]), tmp_path)
    assert isinstance(raised.value, AmbiguousSplitSecurityError)
    db_session.rollback()
    assert db_session.query(StockSplit).count() == 0
    assert db_session.query(IngestionRun).count() == 0, "a usage error opens no run"


@pytest.mark.anyio
async def test_narrowing_a_reused_ticker_to_one_holder_works(
    db_session: Session, exchange: Exchange, tmp_path: Path
) -> None:
    first = _security(db_session, "First Holder")
    second = _security(db_session, "Second Holder")
    _listing(db_session, first, exchange, "NVDA", date(2000, 1, 3), date(2024, 5, 1))
    _listing(db_session, second, exchange, "NVDA", date(2024, 5, 1))
    db_session.commit()

    await _ingest(
        db_session,
        StubProvider([_split()]),
        tmp_path,
        start=date(2024, 5, 1),
        end=date(2024, 12, 31),
    )

    assert db_session.query(StockSplit).one().security_id == second.id


@pytest.mark.anyio
async def test_two_listings_of_the_same_security_are_one_security(
    db_session: Session, exchange: Exchange, tmp_path: Path
) -> None:
    security = _security(db_session, "Dual")
    other = Exchange(mic="ARCX", name="Arca test", timezone="America/New_York")
    db_session.add(other)
    db_session.flush()
    _listing(db_session, security, exchange, "DUAL", date(2000, 1, 3))
    _listing(db_session, security, other, "DUAL", date(2000, 1, 3), primary=False)
    db_session.commit()

    split = _split(ticker="DUAL", provider_split_id="E-dual")
    await _ingest(db_session, StubProvider([split]), tmp_path, ticker="DUAL")

    assert db_session.query(StockSplit).one().security_id == security.id
    assert db_session.query(StockSplitCoverage).count() == 1, "one security, one era"


# ── failed runs ─────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_a_provider_failure_keeps_a_durable_failed_run_and_no_domain_rows(
    nvda: Security, db_session: Session, tmp_path: Path
) -> None:
    provider = StubProvider([], error=ProviderServerError(503))
    service = _service(db_session, provider, tmp_path)
    with pytest.raises(ProviderServerError):
        await service.ingest_stock_splits("NVDA", START, END, provider_history_start=None)
    db_session.commit()

    run = db_session.query(IngestionRun).one()
    assert run.status is IngestionStatus.FAILED and run.records_written == 0
    assert db_session.query(StockSplit).count() == 0
    assert db_session.query(StockSplitCoverage).count() == 0


@pytest.mark.anyio
async def test_a_rejected_record_fails_the_run_records_no_coverage_and_keeps_the_evidence(
    nvda: Security, db_session: Session, tmp_path: Path
) -> None:
    provider = StubProvider([_split()], rejected=("split Ebad: invalid split record",))
    service = _service(db_session, provider, tmp_path)
    with pytest.raises(IncompleteSplitFetchError, match="no coverage"):
        await service.ingest_stock_splits("NVDA", START, END, provider_history_start=None)
    db_session.commit()

    run = db_session.query(IngestionRun).one()
    assert run.status is IngestionStatus.FAILED
    assert (run.records_received, run.records_failed) == (2, 1)
    assert db_session.query(StockSplit).count() == 0, "a good record is not stored beside a bad one"
    assert db_session.query(StockSplitCoverage).count() == 0
    evidence = [str(p) for p in (tmp_path / "raw").rglob("*") if p.is_file()]
    assert any("stock-splits-rejected" in p for p in evidence)
    assert not any("/stock-splits/" in p for p in evidence), "rejected bytes stay out of accepted"


@pytest.mark.anyio
async def test_a_conflict_mid_run_rolls_back_the_whole_run_including_coverage(
    nvda: Security, db_session: Session, tmp_path: Path
) -> None:
    await _ingest(db_session, StubProvider([_split()]), tmp_path)
    fresh = _split(provider_split_id="E-new", execution_date=date(2024, 9, 1))
    conflicting = _split(new_shares=Decimal(40))
    service = _service(db_session, StubProvider([fresh, conflicting]), tmp_path)
    with pytest.raises(StockSplitConflictError):
        await service.ingest_stock_splits(
            "NVDA", date(2024, 1, 1), date(2026, 1, 1), provider_history_start=None
        )
    db_session.commit()

    assert [r.provider_split_id for r in db_session.query(StockSplit)] == ["E-nvda-2024"]
    assert db_session.query(StockSplitCoverage).count() == 1, "only the first run's coverage"
    assert db_session.query(IngestionRun).filter_by(status=IngestionStatus.FAILED).count() == 1


# ── range validation ────────────────────────────────────────────────────────


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("start", "end", "message"),
    [
        (date(2024, 2, 1), date(2024, 1, 1), "precedes"),
        (date(2024, 1, 1), NOW.date(), "within 7 days"),
        (date(2024, 1, 1), NOW.date() - timedelta(days=6), "within 7 days"),
        (date(2024, 1, 1), date(2027, 1, 1), "within 7 days"),
    ],
)
async def test_the_range_must_be_ordered_and_safely_in_the_past(
    nvda: Security,
    db_session: Session,
    tmp_path: Path,
    start: date,
    end: date,
    message: str,
) -> None:
    service = _service(db_session, StubProvider([]), tmp_path)
    with pytest.raises(InvalidSplitRangeError, match=message):
        await service.ingest_stock_splits("NVDA", start, end, provider_history_start=None)
    assert db_session.query(IngestionRun).count() == 0, "an invalid request opens no run"


@pytest.mark.anyio
async def test_a_start_before_the_plans_history_is_refused_by_the_service_itself(
    nvda: Security, db_session: Session, tmp_path: Path
) -> None:
    service = _service(db_session, StubProvider([]), tmp_path)
    with pytest.raises(InvalidSplitRangeError, match="provider plan"):
        await service.ingest_stock_splits(
            "NVDA", date(2015, 1, 1), date(2016, 1, 1), provider_history_start=date(2024, 10, 3)
        )
    assert db_session.query(IngestionRun).count() == 0
    assert db_session.query(StockSplitCoverage).count() == 0


@pytest.mark.anyio
async def test_the_margin_is_measured_in_utc_not_the_callers_local_date(
    nvda: Security, db_session: Session, tmp_path: Path
) -> None:
    # 2026-10-10 00:30 at UTC+05:30 is still 2026-10-09 19:00 UTC.
    local_now = datetime(2026, 10, 10, 0, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    service = SplitIngestionService(
        session=db_session,
        provider=StubProvider([]),
        store=RawResponseStore(root=tmp_path / "raw"),
        now=local_now,
    )
    # Seven days before the UTC date is 2026-10-02; the local date would allow 2026-10-03.
    with pytest.raises(InvalidSplitRangeError, match="2026-10-02"):
        await service.ingest_stock_splits(
            "NVDA", date(2026, 9, 1), date(2026, 10, 3), provider_history_start=None
        )


@pytest.mark.anyio
async def test_the_latest_claimable_day_is_exactly_the_safety_margin_back(
    nvda: Security, db_session: Session, tmp_path: Path
) -> None:
    edge = NOW.date() - timedelta(days=COVERAGE_SAFETY_MARGIN_DAYS)
    await _ingest(db_session, StubProvider([]), tmp_path, start=date(2026, 9, 1), end=edge)

    assert db_session.query(StockSplitCoverage).one().covered_to == edge
    with pytest.raises(InvalidSplitRangeError):
        await _service(db_session, StubProvider([]), tmp_path).ingest_stock_splits(
            "NVDA", date(2026, 9, 1), edge + timedelta(days=1), provider_history_start=None
        )


# ── coverage: absence is not evidence ───────────────────────────────────────


def _massive_source(session: Session) -> DataSource:
    return get_or_create_data_source(
        session, name="massive", provider_type=ProviderType.MARKET_DATA_VENDOR
    )


def _guard(session: Session, security: Security, entry: date, exit_: date):  # type: ignore[no-untyped-def]
    return StockSplitRepository(session).guard(
        security.id,
        coverage_source_id=_massive_source(session).id,
        start_session=entry,
        end_session=exit_,
    )


def test_an_empty_database_is_never_clear(nvda: Security, db_session: Session) -> None:
    result = _guard(db_session, nvda, date(2024, 6, 5), date(2024, 6, 14))
    assert result.status is GuardStatus.COVERAGE_UNAVAILABLE
    assert not result.is_clear


@pytest.mark.anyio
async def test_covered_range_with_no_split_is_clear_only_inside_the_range(
    nvda: Security, db_session: Session, tmp_path: Path
) -> None:
    await _ingest(
        db_session, StubProvider([]), tmp_path, start=date(2024, 1, 1), end=date(2024, 12, 31)
    )

    inside = _guard(db_session, nvda, date(2024, 6, 5), date(2024, 6, 14))
    assert inside.status is GuardStatus.CLEAR
    straddling = _guard(db_session, nvda, date(2024, 12, 20), date(2025, 1, 10))
    assert straddling.status is GuardStatus.COVERAGE_UNAVAILABLE
    assert straddling.uncovered_ranges[0].start == date(2025, 1, 1)
    before = _guard(db_session, nvda, date(2023, 12, 20), date(2024, 1, 10))
    assert before.status is GuardStatus.COVERAGE_UNAVAILABLE


@pytest.mark.anyio
async def test_covered_range_with_a_split_blocks_with_the_split_named(
    nvda: Security, db_session: Session, tmp_path: Path
) -> None:
    await _ingest(db_session, StubProvider([_split()]), tmp_path)

    entry_before = _guard(db_session, nvda, date(2024, 6, 7), date(2024, 6, 14))
    assert entry_before.status is GuardStatus.BLOCKED_BY_SPLIT
    assert entry_before.blocking_splits[0].provider_split_id == "E-nvda-2024"
    on_entry = _guard(db_session, nvda, date(2024, 6, 10), date(2024, 6, 14))
    assert on_entry.status is GuardStatus.CLEAR
    on_exit = _guard(db_session, nvda, date(2024, 6, 5), date(2024, 6, 10))
    assert on_exit.status is GuardStatus.BLOCKED_BY_SPLIT


@pytest.mark.anyio
async def test_coverage_is_clamped_to_the_dates_the_security_held_the_ticker(
    db_session: Session, exchange: Exchange, tmp_path: Path
) -> None:
    security = _security(db_session, "Renamer")
    _listing(db_session, security, exchange, "OLDN", date(2010, 1, 1), date(2024, 3, 1))
    _listing(db_session, security, exchange, "NEWN", date(2024, 3, 1))
    db_session.commit()

    await _ingest(
        db_session,
        StubProvider([]),
        tmp_path,
        ticker="NEWN",
        start=date(2023, 1, 1),
        end=date(2025, 1, 1),
    )

    (coverage,) = db_session.query(StockSplitCoverage).all()
    assert (coverage.covered_from, coverage.covered_to) == (date(2024, 3, 1), date(2025, 1, 1))
    # The OLDN era was never fetched, so it is not covered even though NEWN was.
    pre_rename = _guard(db_session, security, date(2024, 1, 10), date(2024, 1, 20))
    assert pre_rename.status is GuardStatus.COVERAGE_UNAVAILABLE


@pytest.mark.anyio
async def test_a_failed_run_leaves_no_coverage_so_the_guard_stays_unavailable(
    nvda: Security, db_session: Session, tmp_path: Path
) -> None:
    service = _service(db_session, StubProvider([], error=ProviderServerError(500)), tmp_path)
    with pytest.raises(ProviderServerError):
        await service.ingest_stock_splits("NVDA", START, END, provider_history_start=None)
    db_session.commit()

    assert _guard(db_session, nvda, date(2024, 6, 5), date(2024, 6, 14)).status is (
        GuardStatus.COVERAGE_UNAVAILABLE
    )


@pytest.mark.anyio
async def test_the_other_securitys_split_does_not_block(
    nvda: Security, db_session: Session, exchange: Exchange, tmp_path: Path
) -> None:
    other = _security(db_session, "Other")
    _listing(db_session, other, exchange, "OTHR", date(2000, 1, 3))
    db_session.commit()
    split = _split(ticker="OTHR", provider_split_id="E-oth")
    await _ingest(db_session, StubProvider([split]), tmp_path, ticker="OTHR")
    await _ingest(db_session, StubProvider([]), tmp_path)

    assert _guard(db_session, nvda, date(2024, 6, 5), date(2024, 6, 14)).status is GuardStatus.CLEAR
    assert _guard(db_session, other, date(2024, 6, 5), date(2024, 6, 14)).status is (
        GuardStatus.BLOCKED_BY_SPLIT
    )


# ── isolation: a known split changes no price and no research output ────────


@pytest.mark.anyio
async def test_ingesting_splits_changes_no_price_bar(
    nvda: Security, db_session: Session, tmp_path: Path
) -> None:
    source = DataSource(name="bars-vendor", provider_type=ProviderType.MARKET_DATA_VENDOR)
    db_session.add(source)
    db_session.flush()
    listing = db_session.query(Listing).one()
    for basis in (PriceBasis.SPLIT_ADJUSTED, PriceBasis.AS_TRADED):
        db_session.add(
            DailyPriceBar(
                price_basis=basis,
                listing_id=listing.id,
                trading_date=date(2024, 6, 7),
                open=Decimal("1200"),
                high=Decimal("1210"),
                low=Decimal("1190"),
                close=Decimal("1200"),
                source_id=source.id,
            )
        )
    db_session.commit()

    def snapshot() -> list[tuple[object, ...]]:
        rows = db_session.execute(
            sa.text(
                "SELECT price_basis, trading_date, open, high, low, close FROM daily_price_bar "
                "ORDER BY price_basis"
            )
        )
        return [tuple(r) for r in rows]

    before = snapshot()
    await _ingest(db_session, StubProvider([_split()]), tmp_path)

    assert snapshot() == before
    assert json.dumps([str(x) for x in before]) == json.dumps([str(x) for x in snapshot()])


@pytest.mark.anyio
async def test_the_real_massive_client_feeds_the_service_end_to_end(
    nvda: Security, db_session: Session, tmp_path: Path
) -> None:
    body = json.dumps(
        {
            "status": "OK",
            "request_id": "r",
            "results": [
                {
                    "id": "E-nvda-2024",
                    "ticker": "NVDA",
                    "execution_date": "2024-06-10",
                    "split_from": 1.0,
                    "split_to": 10.0,
                    "adjustment_type": "forward_split",
                    "historical_adjustment_factor": 0.1,
                }
            ],
        }
    ).encode()

    async def no_sleep(_seconds: float) -> None:
        return None

    client = MassiveClient(
        MassiveConfig(api_key="test-key-0123456789", base_url="https://api.massive.com"),
        transport=httpx.MockTransport(lambda _r: httpx.Response(200, content=body)),
        sleep=no_sleep,
    )
    async with client:
        service = SplitIngestionService(
            session=db_session,
            provider=client,
            store=RawResponseStore(root=tmp_path / "raw"),
            now=NOW,
        )
        await service.ingest_stock_splits("NVDA", START, END, provider_history_start=None)
    db_session.commit()

    stored = db_session.query(StockSplit).one()
    assert (stored.old_shares, stored.new_shares) == (Decimal(1), Decimal(10))
    assert any(p.read_bytes() == body for p in (tmp_path / "raw").rglob("*") if p.is_file())
