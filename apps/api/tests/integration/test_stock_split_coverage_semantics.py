"""Coverage semantics against real PostgreSQL: what may and may not prove "no split".

Absence of ``stock_split`` rows is never evidence. Only ``stock_split_coverage`` written
by a complete, clean run of the SAME source, for the SAME security, over the dates in
question, can let the guard return CLEAR.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from quantforge.ingestion.canonical import CanonicalStockSplit
from quantforge.ingestion.raw_store import RawResponseStore
from quantforge.ingestion.sources import get_or_create_data_source
from quantforge.ingestion.splits import SplitIngestionService
from quantforge.market import GuardStatus
from quantforge.models.company import Company
from quantforge.models.corporate_action import StockSplit, StockSplitCoverage
from quantforge.models.enums import (
    IngestionStatus,
    ProviderType,
    SecurityType,
    StockSplitAdjustmentType,
)
from quantforge.models.ingestion import IngestionRun
from quantforge.models.provenance import DataSource
from quantforge.models.security import Exchange, Listing, Security
from quantforge.providers.base import RawCapture
from quantforge.providers.splits import SplitsResult
from quantforge.repositories.stock_split import StockSplitRepository

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
ENTRY, EXIT = date(2024, 6, 5), date(2024, 6, 14)


class Stub:
    name = "massive"

    def __init__(self, splits: list[CanonicalStockSplit]) -> None:
        self._splits = splits

    async def get_stock_splits(self, ticker: str) -> SplitsResult:
        del ticker
        capture = RawCapture(
            provider="massive",
            dataset="stock-splits",
            endpoint="/stocks/v1/splits",
            request_parameters={"ticker": "COV"},
            body=b'{"status":"OK","results":[]}',
            retrieved_at=NOW,
        )
        return SplitsResult(splits=self._splits, captures=(capture,))


def _split(pid: str = "E1", on: date = date(2024, 6, 10)) -> CanonicalStockSplit:
    return CanonicalStockSplit(
        provider_split_id=pid,
        ticker="COV",
        execution_date=on,
        old_shares=Decimal(1),
        new_shares=Decimal(10),
        adjustment_type=StockSplitAdjustmentType.FORWARD_SPLIT,
    )


def _make_security(session: Session, exchange: Exchange, name: str, ticker: str) -> Security:
    company = Company(legal_name=f"{name} Inc.", display_name=name)
    session.add(company)
    session.flush()
    security = Security(
        company_id=company.id, security_type=SecurityType.EQUITY, name=name, currency="USD"
    )
    session.add(security)
    session.flush()
    session.add(
        Listing(
            security_id=security.id,
            exchange_id=exchange.id,
            ticker=ticker,
            valid_from=date(2000, 1, 3),
            is_primary=True,
        )
    )
    session.flush()
    return security


@pytest.fixture
def exchange(db_session: Session) -> Exchange:
    row = Exchange(mic="XNAS", name="Nasdaq test", timezone="America/New_York")
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def cov(db_session: Session, exchange: Exchange) -> Security:
    security = _make_security(db_session, exchange, "Cov", "COV")
    db_session.commit()
    return security


async def _ingest(session: Session, provider: Stub, tmp_path: Path) -> None:
    service = SplitIngestionService(
        session=session,
        provider=provider,
        store=RawResponseStore(root=tmp_path / "raw"),
        now=NOW,
    )
    await service.ingest_stock_splits(
        "COV", date(2024, 1, 1), date(2024, 12, 31), provider_history_start=None
    )
    session.commit()


def _guard(session: Session, security: Security, source: DataSource):  # type: ignore[no-untyped-def]
    return StockSplitRepository(session).guard(
        security.id, coverage_source_id=source.id, start_session=ENTRY, end_session=EXIT
    )


def _source(session: Session, name: str = "massive") -> DataSource:
    return get_or_create_data_source(
        session, name=name, provider_type=ProviderType.MARKET_DATA_VENDOR
    )


def _add_coverage(
    session: Session, security: Security, source: DataSource, start: date, end: date
) -> None:
    session.add(
        StockSplitCoverage(
            security_id=security.id,
            source_id=source.id,
            provider_ticker="COV",
            covered_from=start,
            covered_to=end,
        )
    )
    session.commit()


def test_a_database_with_no_rows_at_all_is_never_clear(db_session: Session, cov: Security) -> None:
    assert _guard(db_session, cov, _source(db_session)).status is GuardStatus.COVERAGE_UNAVAILABLE


def test_coverage_of_another_security_proves_nothing(
    db_session: Session, exchange: Exchange, cov: Security
) -> None:
    other = _make_security(db_session, exchange, "Other", "OTH")
    source = _source(db_session)
    _add_coverage(db_session, other, source, date(2024, 1, 1), date(2024, 12, 31))

    assert _guard(db_session, cov, source).status is GuardStatus.COVERAGE_UNAVAILABLE


def test_coverage_from_another_source_does_not_prove_absence_for_this_one(
    db_session: Session, cov: Security
) -> None:
    massive, other = _source(db_session), _source(db_session, "other-vendor")
    _add_coverage(db_session, cov, other, date(2024, 1, 1), date(2024, 12, 31))

    assert _guard(db_session, cov, massive).status is GuardStatus.COVERAGE_UNAVAILABLE
    assert _guard(db_session, cov, other).status is GuardStatus.CLEAR


def test_a_split_from_any_source_blocks_even_with_the_asking_sources_full_coverage(
    db_session: Session, cov: Security
) -> None:
    massive, other = _source(db_session), _source(db_session, "other-vendor")
    _add_coverage(db_session, cov, massive, date(2024, 1, 1), date(2024, 12, 31))
    db_session.add(
        StockSplit(
            security_id=cov.id,
            source_id=other.id,
            provider_split_id="X1",
            provider_ticker="COV",
            execution_date=date(2024, 6, 10),
            old_shares=Decimal(1),
            new_shares=Decimal(2),
            adjustment_type=StockSplitAdjustmentType.FORWARD_SPLIT,
        )
    )
    db_session.commit()

    assert _guard(db_session, cov, massive).status is GuardStatus.BLOCKED_BY_SPLIT


def test_adjacent_overlapping_and_duplicate_rows_combine_without_inventing_coverage(
    db_session: Session, cov: Security
) -> None:
    source = _source(db_session)
    _add_coverage(db_session, cov, source, date(2024, 6, 1), date(2024, 6, 9))
    _add_coverage(db_session, cov, source, date(2024, 6, 10), date(2024, 6, 12))  # adjacent
    _add_coverage(db_session, cov, source, date(2024, 6, 8), date(2024, 6, 11))  # overlapping
    assert _guard(db_session, cov, source).status is GuardStatus.COVERAGE_UNAVAILABLE

    _add_coverage(db_session, cov, source, date(2024, 6, 13), date(2024, 6, 14))
    assert _guard(db_session, cov, source).status is GuardStatus.CLEAR


@pytest.mark.anyio
async def test_a_known_split_blocks_even_when_coverage_is_incomplete(
    db_session: Session, cov: Security, tmp_path: Path
) -> None:
    # Covered only up to 2024-06-08, so the exit end of the holding is unproven...
    service = SplitIngestionService(
        session=db_session,
        provider=Stub([_split()]),
        store=RawResponseStore(root=tmp_path / "raw"),
        now=NOW,
    )
    await service.ingest_stock_splits(
        "COV", date(2024, 1, 1), date(2024, 6, 8), provider_history_start=None
    )
    db_session.commit()
    # ...the split (06-10) was not in that fetch, so insert it as a separately known fact.
    db_session.add(
        StockSplit(
            security_id=cov.id,
            source_id=_source(db_session).id,
            provider_split_id="E-known",
            provider_ticker="COV",
            execution_date=date(2024, 6, 10),
            old_shares=Decimal(1),
            new_shares=Decimal(10),
            adjustment_type=StockSplitAdjustmentType.FORWARD_SPLIT,
        )
    )
    db_session.commit()

    result = _guard(db_session, cov, _source(db_session))
    assert result.status is GuardStatus.BLOCKED_BY_SPLIT
    assert result.uncovered_ranges == (), "blocked on the known fact, not on a coverage gap"


@pytest.mark.anyio
async def test_two_provider_ids_for_one_economic_split_block_twice_and_cover_normally(
    db_session: Session, cov: Security, tmp_path: Path
) -> None:
    await _ingest(db_session, Stub([_split("A"), _split("B")]), tmp_path)

    assert db_session.query(StockSplit).count() == 2
    assert db_session.query(StockSplitCoverage).count() == 1
    result = _guard(db_session, cov, _source(db_session))
    assert result.status is GuardStatus.BLOCKED_BY_SPLIT
    assert [s.provider_split_id for s in result.blocking_splits] == ["A", "B"]


class _FailingStore(RawResponseStore):
    def store_all(self, captures):  # type: ignore[no-untyped-def]
        raise OSError("disk full")


@pytest.mark.anyio
async def test_a_raw_storage_failure_fails_the_run_and_writes_no_split_or_coverage(
    db_session: Session, cov: Security, tmp_path: Path
) -> None:
    service = SplitIngestionService(
        session=db_session,
        provider=Stub([_split()]),
        store=_FailingStore(root=tmp_path / "raw"),
        now=NOW,
    )
    with pytest.raises(OSError, match="disk full"):
        await service.ingest_stock_splits(
            "COV", date(2024, 1, 1), date(2024, 12, 31), provider_history_start=None
        )
    db_session.commit()

    assert db_session.query(IngestionRun).one().status is IngestionStatus.FAILED
    assert db_session.query(StockSplit).count() == 0
    assert db_session.query(StockSplitCoverage).count() == 0
    assert _guard(db_session, cov, _source(db_session)).status is GuardStatus.COVERAGE_UNAVAILABLE


@pytest.mark.anyio
async def test_the_covered_range_is_what_was_requested_and_clean_not_the_fetch_window(
    db_session: Session, cov: Security, tmp_path: Path
) -> None:
    await _ingest(db_session, Stub([]), tmp_path)

    (row,) = db_session.query(StockSplitCoverage).all()
    assert (row.covered_from, row.covered_to) == (date(2024, 1, 1), date(2024, 12, 31))
    assert _guard(db_session, cov, _source(db_session)).status is GuardStatus.CLEAR
    # A holding that extends one day past the covered range is unproven.
    result = StockSplitRepository(db_session).guard(
        cov.id,
        coverage_source_id=_source(db_session).id,
        start_session=date(2024, 12, 20),
        end_session=date(2025, 1, 2),
    )
    assert result.status is GuardStatus.COVERAGE_UNAVAILABLE
