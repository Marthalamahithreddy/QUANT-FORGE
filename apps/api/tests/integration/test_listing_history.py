"""Sourced listing history against real PostgreSQL: facts, the one resolver, and every consumer.

The invariant: ``Listing.valid_from`` means "first observed", never "listed since". Source-stated
facts can prove earlier dates, but a gap, an ambiguity or a conflict is never turned into
continuity -- and research alignment, earnings ingestion, the earnings API and split ingestion all
read the same resolver, so they cannot disagree.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from quantforge.core.config import Settings
from quantforge.db.session import get_session
from quantforge.identity.listing_history import Evidence, ResolutionStatus
from quantforge.identity.resolver import HistoricalListingResolver
from quantforge.ingestion.canonical import (
    CanonicalStockSplit,
    CanonicalTickerChange,
    CanonicalTickerEvents,
    CanonicalTickerSnapshot,
)
from quantforge.ingestion.earnings import UnknownIssuerError, resolve_company_id
from quantforge.ingestion.listing_history import (
    IncompleteListingHistoryError,
    InvalidAnchorDateError,
    ListingFactConflictError,
    ListingHistoryIngestionService,
)
from quantforge.ingestion.raw_store import RawResponseStore
from quantforge.ingestion.splits import (
    ListingHistoryConflictError,
    RetiredTickerCoverageError,
    ReusedTickerRangeError,
    SplitIngestionService,
    UnknownSplitTickerError,
)
from quantforge.main import create_app
from quantforge.models.company import Company
from quantforge.models.corporate_action import StockSplitCoverage
from quantforge.models.earnings import EarningsEvent
from quantforge.models.enums import (
    EarningsTiming,
    IdentifierType,
    IngestionStatus,
    ListingFactKind,
    ProviderType,
    SecurityType,
    StockSplitAdjustmentType,
)
from quantforge.models.ingestion import IngestionRun
from quantforge.models.listing_history import ListingHistoryFact
from quantforge.models.provenance import DataSource
from quantforge.models.security import Exchange, Listing, Security, SecurityIdentifier
from quantforge.providers.base import RawCapture
from quantforge.providers.errors import ProviderServerError
from quantforge.providers.listing_history import TickerEventsResult, TickerSnapshotsResult
from quantforge.providers.splits import SplitsResult
from quantforge.research.alignment import align_earnings_event
from quantforge.research.availability import ResearchMode
from quantforge.research.listing_resolution import (
    ListingResolutionStatus,
    resolve_company_listing_for_date,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
FIRST_OBSERVED = date(2026, 10, 2)
FIGI_A = "BBG001SCTQY4"
FIGI_B = "BBG001SCTQY5"
COMPOSITE = "BBG000BWQYZ5"
D = date


def _source(session: Session, name: str = "massive") -> DataSource:
    row = session.scalar(sa.select(DataSource).where(DataSource.name == name))
    if row is None:
        row = DataSource(name=name, provider_type=ProviderType.MARKET_DATA_VENDOR)
        session.add(row)
        session.flush()
    return row


@pytest.fixture
def exchange(db_session: Session) -> Exchange:
    row = Exchange(mic="XNAS", name="Nasdaq test", timezone="America/New_York")
    db_session.add(row)
    db_session.commit()
    return row


class World:
    """Builds issuers, share classes, FIGIs and first-observed listings."""

    def __init__(self, session: Session, exchange: Exchange) -> None:
        self.session = session
        self.exchange = exchange

    def security(
        self,
        name: str,
        figi: str | None,
        *,
        cik: str | None = None,
        figi_type: IdentifierType = IdentifierType.FIGI_SHARE_CLASS,
        company: Company | None = None,
    ) -> Security:
        company = company or Company(legal_name=f"{name} Inc.", display_name=name, cik=cik)
        self.session.add(company)
        self.session.flush()
        security = Security(
            company_id=company.id, security_type=SecurityType.EQUITY, name=name, currency="USD"
        )
        self.session.add(security)
        self.session.flush()
        if figi is not None:
            self.session.add(
                SecurityIdentifier(
                    security_id=security.id,
                    identifier_type=figi_type,
                    identifier_value=figi,
                    source_id=_source(self.session).id,
                )
            )
        self.session.flush()
        return security

    def listing(
        self,
        security: Security,
        ticker: str,
        valid_from: date = FIRST_OBSERVED,
        valid_to: date | None = None,
        *,
        primary: bool | None = None,
        mic: str | None = None,
    ) -> Listing:
        exchange = self.exchange
        if mic is not None:
            exchange = self.session.scalar(
                sa.select(Exchange).where(Exchange.mic == mic)
            ) or Exchange(mic=mic, name=f"{mic} test", timezone="America/New_York")
            self.session.add(exchange)
            self.session.flush()
        row = Listing(
            security_id=security.id,
            exchange_id=exchange.id,
            ticker=ticker,
            valid_from=valid_from,
            valid_to=valid_to,
            is_primary=(valid_to is None) if primary is None else primary,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def fact(
        self,
        security: Security,
        kind: ListingFactKind,
        ticker: str,
        on: date,
        *,
        source: str = "massive",
        mic: str | None = None,
    ) -> ListingHistoryFact:
        row = ListingHistoryFact(
            security_id=security.id,
            source_id=_source(self.session, source).id,
            kind=kind,
            ticker=ticker,
            effective_date=on,
            mic=mic,
        )
        self.session.add(row)
        self.session.flush()
        return row


@pytest.fixture
def world(db_session: Session, exchange: Exchange) -> World:
    return World(db_session, exchange)


STARTED, OBSERVED, LAST = (
    ListingFactKind.TICKER_STARTED,
    ListingFactKind.TICKER_OBSERVED,
    ListingFactKind.LAST_TRADED,
)


def _meta(world: World) -> Security:
    """FB -> META: share-class facts say FB from 2012-05-18, META from 2022-06-09; one first-observed META listing."""
    meta = world.security("Meta", FIGI_A, cik="0001326801")
    world.listing(meta, "META")
    world.fact(meta, STARTED, "FB", D(2012, 5, 18))
    world.fact(meta, STARTED, "META", D(2022, 6, 9))
    world.session.commit()
    return meta


# ── the core provenance rule ────────────────────────────────────────────────


def test_a_first_observed_listing_alone_proves_nothing_before_its_valid_from(
    db_session: Session, world: World
) -> None:
    aapl = world.security("Apple", FIGI_A, cik="0000320193")
    world.listing(aapl, "AAPL")
    db_session.commit()
    resolver = HistoricalListingResolver(db_session)

    assert resolver.resolve_ticker_on("AAPL", D(2020, 1, 2)).status is ResolutionStatus.NO_EVIDENCE
    today = resolver.resolve_ticker_on("AAPL", FIRST_OBSERVED)
    assert today.status is ResolutionStatus.RESOLVED
    assert today.evidence is Evidence.PROVISIONAL_FIRST_OBSERVED


def test_sourced_history_proves_an_earlier_date_and_the_provisional_row_is_untouched(
    db_session: Session, world: World
) -> None:
    aapl = world.security("Apple", FIGI_A, cik="0000320193")
    row = world.listing(aapl, "AAPL")
    world.fact(aapl, STARTED, "AAPL", D(1980, 12, 12))
    db_session.commit()

    outcome = HistoricalListingResolver(db_session).resolve_ticker_on("AAPL", D(2020, 1, 2))

    assert outcome.status is ResolutionStatus.RESOLVED
    assert outcome.security_id == aapl.id and outcome.evidence is Evidence.SOURCE_STATED_HISTORY
    db_session.refresh(row)
    assert row.valid_from == FIRST_OBSERVED, "first-observed knowledge is never backdated"
    assert db_session.query(ListingHistoryFact).count() == 1, "the source fact stays inspectable"


def test_the_same_date_without_sourced_history_remains_unresolved(
    db_session: Session, world: World
) -> None:
    other = world.security("Other", FIGI_B)
    world.listing(other, "OTH")
    db_session.commit()
    assert (
        HistoricalListingResolver(db_session).resolve_ticker_on("OTH", D(2020, 1, 2)).status
        is ResolutionStatus.NO_EVIDENCE
    )


# ── rename ───────────────────────────────────────────────────────────────────


def test_fb_to_meta_old_dates_resolve_the_old_ticker_and_new_dates_the_new_one(
    db_session: Session, world: World
) -> None:
    meta = _meta(world)
    resolver = HistoricalListingResolver(db_session)

    old = resolver.resolve_security_on(meta.id, D(2018, 3, 1))
    new = resolver.resolve_security_on(meta.id, D(2024, 3, 1))

    assert (old.status, old.ticker) == (ResolutionStatus.RESOLVED, "FB")
    assert old.listing_id is None, "no stored listing carries FB: identity known, listing not"
    assert (new.status, new.ticker, new.evidence) == (
        ResolutionStatus.RESOLVED,
        "META",
        Evidence.SOURCE_STATED_HISTORY,
    )
    assert new.listing_id is not None
    assert resolver.resolve_ticker_on("FB", D(2018, 3, 1)).security_id == meta.id
    assert resolver.resolve_ticker_on("FB", D(2024, 3, 1)).status is ResolutionStatus.NO_EVIDENCE
    assert resolver.resolve_ticker_on("META", D(2018, 3, 1)).status is ResolutionStatus.NO_EVIDENCE


def test_the_current_ticker_never_rewrites_an_old_observation(
    db_session: Session, world: World
) -> None:
    meta = _meta(world)
    resolver = HistoricalListingResolver(db_session)

    for day in (D(2012, 5, 18), D(2020, 1, 2), D(2022, 6, 8)):
        assert resolver.resolve_security_on(meta.id, day).ticker == "FB"
    assert (
        resolver.resolve_security_on(meta.id, D(2012, 5, 17)).status is ResolutionStatus.NO_EVIDENCE
    )


# ── ticker reuse ─────────────────────────────────────────────────────────────


def _reused(world: World) -> tuple[Security, Security]:
    a = world.security("First", FIGI_A)
    b = world.security("Second", FIGI_B)
    world.fact(a, STARTED, "ABC", D(2005, 1, 3))
    world.fact(a, LAST, "ABC", D(2014, 12, 31))
    world.fact(b, STARTED, "ABC", D(2016, 3, 1))
    world.listing(b, "ABC")
    world.session.commit()
    return a, b


def test_a_reused_ticker_resolves_to_the_security_that_held_it_on_each_date(
    db_session: Session, world: World
) -> None:
    a, b = _reused(world)
    resolver = HistoricalListingResolver(db_session)

    assert resolver.resolve_ticker_on("ABC", D(2010, 6, 1)).security_id == a.id
    assert resolver.resolve_ticker_on("ABC", D(2020, 6, 1)).security_id == b.id
    assert resolver.resolve_ticker_on("ABC", D(2015, 6, 1)).status is ResolutionStatus.NO_EVIDENCE


def test_a_range_spanning_both_holders_does_not_claim_one_security_continuity(
    db_session: Session, world: World
) -> None:
    _reused(world)
    resolver = HistoricalListingResolver(db_session)

    spanning = resolver.resolve_ticker_eras("ABC", D(2010, 1, 1), D(2020, 12, 31))
    narrow = resolver.resolve_ticker_eras("ABC", D(2016, 3, 1), D(2020, 12, 31))

    assert spanning.status is ResolutionStatus.AMBIGUOUS and spanning.ranges == ()
    assert narrow.status is ResolutionStatus.RESOLVED and len(narrow.ranges) == 1


# ── gaps, overlaps, conflicts, dual listings ────────────────────────────────


def test_a_gap_between_proven_eras_is_never_filled(db_session: Session, world: World) -> None:
    sec = world.security("Gappy", FIGI_A)
    world.fact(sec, STARTED, "AAA", D(2010, 1, 4))
    world.fact(sec, LAST, "AAA", D(2015, 3, 31))
    world.fact(sec, STARTED, "BBB", D(2015, 6, 1))
    world.listing(sec, "BBB")
    db_session.commit()
    resolver = HistoricalListingResolver(db_session)

    assert resolver.resolve_security_on(sec.id, D(2015, 3, 31)).ticker == "AAA"
    assert (
        resolver.resolve_security_on(sec.id, D(2015, 4, 15)).status is ResolutionStatus.NO_EVIDENCE
    )
    assert resolver.resolve_security_on(sec.id, D(2015, 6, 1)).ticker == "BBB"


def test_contradicting_evidence_is_a_conflict_not_a_silent_choice(
    db_session: Session, world: World
) -> None:
    sec = world.security("Messy", FIGI_A)
    world.fact(sec, STARTED, "AAA", D(2020, 1, 2), source="massive")
    world.fact(sec, STARTED, "BBB", D(2020, 1, 2), source="other-vendor")
    db_session.commit()
    resolver = HistoricalListingResolver(db_session)

    assert resolver.resolve_security_on(sec.id, D(2020, 1, 2)).status is ResolutionStatus.CONFLICT
    assert resolver.resolve_ticker_on("AAA", D(2020, 1, 2)).status is ResolutionStatus.CONFLICT
    assert resolver.resolve_ticker_eras("AAA", D(2020, 1, 1), D(2020, 1, 31)).status is (
        ResolutionStatus.CONFLICT
    )


def test_two_sources_that_agree_do_not_conflict_and_neither_outranks_the_other(
    db_session: Session, world: World
) -> None:
    sec = world.security("Agreed", FIGI_A)
    for name in ("massive", "other-vendor"):
        world.fact(sec, STARTED, "AAA", D(2010, 1, 4), source=name)
    world.listing(sec, "AAA")
    db_session.commit()

    assert HistoricalListingResolver(db_session).resolve_ticker_on("AAA", D(2015, 1, 5)).status is (
        ResolutionStatus.RESOLVED
    )


def test_a_ticker_on_two_venues_of_one_security_is_ambiguous_unless_one_is_primary(
    db_session: Session, world: World
) -> None:
    sec = world.security("Dual", FIGI_A)
    world.listing(sec, "DUAL", primary=False)
    world.listing(sec, "DUAL", D(2026, 10, 2), mic="ARCX", primary=False)
    world.fact(sec, STARTED, "DUAL", D(2005, 1, 3))
    db_session.commit()

    outcome = HistoricalListingResolver(db_session).resolve_security_on(sec.id, D(2020, 1, 2))

    assert outcome.status is ResolutionStatus.AMBIGUOUS, "venue history is not established"


def test_a_single_primary_listing_resolves_a_dual_listed_ticker(
    db_session: Session, world: World
) -> None:
    sec = world.security("Dual", FIGI_A)
    primary = world.listing(sec, "DUAL")
    world.listing(sec, "DUAL", D(2026, 10, 2), mic="ARCX", primary=False)
    world.fact(sec, STARTED, "DUAL", D(2005, 1, 3))
    db_session.commit()

    outcome = HistoricalListingResolver(db_session).resolve_security_on(sec.id, D(2020, 1, 2))

    assert outcome.status is ResolutionStatus.RESOLVED and outcome.listing_id == primary.id


def test_a_composite_or_legacy_figi_never_serves_as_the_share_class_anchor(
    db_session: Session, world: World
) -> None:
    world.security("Legacy", FIGI_A, figi_type=IdentifierType.FIGI)
    world.security("Composite", COMPOSITE, figi_type=IdentifierType.OTHER)
    anchored = world.security("Anchored", FIGI_B)
    db_session.commit()
    resolver = HistoricalListingResolver(db_session)

    assert resolver.security_id_for_share_class_figi(FIGI_A) is None, (
        "legacy level-unspecified FIGI"
    )
    assert resolver.security_id_for_share_class_figi(COMPOSITE) is None
    assert resolver.security_id_for_share_class_figi(FIGI_B) == anchored.id


def test_the_company_cik_is_not_a_security_identity(db_session: Session, world: World) -> None:
    company = Company(legal_name="Two Class Inc.", display_name="Two Class", cik="0000111111")
    one = world.security("Class A", FIGI_A, company=company)
    two = world.security("Class B", FIGI_B, company=company)
    world.listing(one, "TWOA")
    world.listing(two, "TWOB")
    db_session.commit()

    outcome = HistoricalListingResolver(db_session).resolve_company_on(company.id, FIRST_OBSERVED)

    assert outcome.status is ResolutionStatus.AMBIGUOUS, "one CIK, two securities"
    explicit = HistoricalListingResolver(db_session).resolve_company_on(
        company.id, FIRST_OBSERVED, security_id=two.id
    )
    assert explicit.ticker == "TWOB"


# ── ingestion of source facts ───────────────────────────────────────────────


def _snapshot(**overrides: object) -> CanonicalTickerSnapshot:
    fields: dict[str, object] = {
        "as_of_date": D(2020, 1, 2),
        "ticker": "META",
        "active": True,
        "share_class_figi": FIGI_A,
        "composite_figi": COMPOSITE,
        "cik": "0001326801",
        "primary_mic": "XNAS",
    }
    fields.update(overrides)
    return CanonicalTickerSnapshot(**fields)  # type: ignore[arg-type]


class StubListingProvider:
    name = "massive"

    def __init__(
        self,
        snapshots: list[CanonicalTickerSnapshot] | None = None,
        *,
        changes: list[tuple[str, date]] | None = None,
        rejected: tuple[str, ...] = (),
        event_rejected: tuple[str, ...] = (),
        unsupported: int = 0,
        error: Exception | None = None,
    ) -> None:
        capture = RawCapture(
            provider="massive",
            dataset="ticker-snapshots",
            endpoint="/v3/reference/tickers",
            request_parameters={"ticker": "META"},
            body=b'{"status":"OK","results":[]}',
            retrieved_at=NOW,
        )
        event_capture = RawCapture(
            provider="massive",
            dataset="ticker-events",
            endpoint="/vX/reference/tickers/{ticker}/events",
            request_parameters={"ticker": "META"},
            body=b'{"status":"OK","results":{"events":[]}}',
            retrieved_at=NOW,
        )
        self._snapshots = TickerSnapshotsResult(
            snapshots=snapshots or [],
            unsupported=unsupported,
            rejected=rejected,
            captures=(capture,),
        )
        self._events = TickerEventsResult(
            events=CanonicalTickerEvents(
                "META",
                tuple(
                    CanonicalTickerChange(t, d)
                    for t, d in sorted(changes or [], key=lambda c: c[1])
                ),
            ),
            rejected=event_rejected,
            captures=(event_capture,),
        )
        self._error = error

    async def get_ticker_snapshots(
        self, ticker: str, on_date: date, *, active: bool = True
    ) -> TickerSnapshotsResult:
        del ticker, on_date, active
        if self._error is not None:
            raise self._error
        return self._snapshots

    async def get_ticker_events(self, ticker: str) -> TickerEventsResult:
        del ticker
        return self._events


def _service(
    session: Session, provider: StubListingProvider, tmp_path: Path
) -> ListingHistoryIngestionService:
    return ListingHistoryIngestionService(
        session=session, provider=provider, store=RawResponseStore(root=tmp_path / "raw"), now=NOW
    )


@pytest.mark.anyio
async def test_a_snapshot_stores_a_single_dated_observation_with_provenance(
    db_session: Session, world: World, tmp_path: Path
) -> None:
    meta = world.security("Meta", FIGI_A, cik="0001326801")
    db_session.commit()

    outcome = await _service(
        db_session, StubListingProvider([_snapshot()]), tmp_path
    ).ingest_ticker_snapshot("META", D(2020, 1, 2))
    db_session.commit()

    fact = db_session.query(ListingHistoryFact).one()
    assert (fact.security_id, fact.kind, fact.ticker, fact.effective_date, fact.mic) == (
        meta.id,
        ListingFactKind.TICKER_OBSERVED,
        "META",
        D(2020, 1, 2),
        "XNAS",
    )
    run = db_session.query(IngestionRun).one()
    assert (run.provider, run.dataset, run.status) == (
        "massive",
        "ticker-snapshots",
        IngestionStatus.SUCCEEDED,
    )
    assert fact.ingestion_run_id == run.id and fact.ingested_at == NOW
    assert outcome.records_written == 1
    assert db_session.query(Listing).count() == 0, "nothing mutates the listing table"
    stored = [p for p in (tmp_path / "raw").rglob("*") if p.is_file()]
    assert any("ticker-snapshots" in str(p) for p in stored)


@pytest.mark.anyio
async def test_one_snapshot_never_becomes_a_continuous_interval(
    db_session: Session, world: World, tmp_path: Path
) -> None:
    meta = world.security("Meta", FIGI_A)
    db_session.commit()
    await _service(db_session, StubListingProvider([_snapshot()]), tmp_path).ingest_ticker_snapshot(
        "META", D(2020, 1, 2)
    )
    db_session.commit()
    resolver = HistoricalListingResolver(db_session)

    assert resolver.resolve_security_on(meta.id, D(2020, 1, 2)).ticker == "META"
    assert (
        resolver.resolve_security_on(meta.id, D(2020, 1, 3)).status is ResolutionStatus.NO_EVIDENCE
    )
    assert (
        resolver.resolve_security_on(meta.id, D(2020, 1, 1)).status is ResolutionStatus.NO_EVIDENCE
    )


@pytest.mark.anyio
async def test_a_delisted_snapshot_stores_a_last_traded_fact_only(
    db_session: Session, world: World, tmp_path: Path
) -> None:
    world.security("Meta", FIGI_A)
    db_session.commit()
    snapshot = _snapshot(active=False, delisted_date=D(2021, 4, 23))

    await _service(db_session, StubListingProvider([snapshot]), tmp_path).ingest_ticker_snapshot(
        "META", D(2022, 1, 3), active=False
    )
    db_session.commit()

    fact = db_session.query(ListingHistoryFact).one()
    assert (fact.kind, fact.effective_date) == (LAST, D(2021, 4, 23))


@pytest.mark.anyio
async def test_a_rerun_is_idempotent_and_a_changed_venue_is_an_explicit_conflict(
    db_session: Session, world: World, tmp_path: Path
) -> None:
    world.security("Meta", FIGI_A)
    db_session.commit()
    provider = StubListingProvider([_snapshot()])
    await _service(db_session, provider, tmp_path).ingest_ticker_snapshot("META", D(2020, 1, 2))
    db_session.commit()
    await _service(db_session, provider, tmp_path).ingest_ticker_snapshot("META", D(2020, 1, 2))
    db_session.commit()
    assert db_session.query(ListingHistoryFact).count() == 1

    changed = StubListingProvider([_snapshot(primary_mic="XNYS")])
    with pytest.raises(ListingFactConflictError):
        await _service(db_session, changed, tmp_path).ingest_ticker_snapshot("META", D(2020, 1, 2))
    db_session.rollback()
    assert db_session.query(ListingHistoryFact).one().mic == "XNAS"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("snapshot", "message"),
    [
        (_snapshot(share_class_figi=None), "no share-class FIGI"),
        (_snapshot(share_class_figi="BBG0000000ZZ"), "no stored security"),
        (_snapshot(cik="0000999999"), "contradicts the stored company CIK"),
    ],
)
async def test_an_unattributable_record_fails_the_run_and_stores_nothing(
    db_session: Session,
    world: World,
    tmp_path: Path,
    snapshot: CanonicalTickerSnapshot,
    message: str,
) -> None:
    world.security("Meta", FIGI_A, cik="0001326801")
    db_session.commit()
    service = _service(
        db_session, StubListingProvider([_snapshot(as_of_date=D(2020, 1, 3)), snapshot]), tmp_path
    )

    with pytest.raises(IncompleteListingHistoryError, match=message):
        await service.ingest_ticker_snapshot("META", D(2020, 1, 2))
    db_session.commit()

    assert db_session.query(ListingHistoryFact).count() == 0, "no partial history survives"
    run = db_session.query(IngestionRun).one()
    assert run.status is IngestionStatus.FAILED and run.records_written == 0


@pytest.mark.anyio
async def test_a_legacy_unspecified_figi_does_not_attribute_a_snapshot(
    db_session: Session, world: World, tmp_path: Path
) -> None:
    world.security("Meta", FIGI_A, figi_type=IdentifierType.FIGI)
    db_session.commit()

    with pytest.raises(IncompleteListingHistoryError, match="no stored security"):
        await _service(
            db_session, StubListingProvider([_snapshot()]), tmp_path
        ).ingest_ticker_snapshot("META", D(2020, 1, 2))


@pytest.mark.anyio
async def test_rejected_records_keep_their_evidence_apart_and_store_no_fact(
    db_session: Session, world: World, tmp_path: Path
) -> None:
    world.security("Meta", FIGI_A)
    db_session.commit()
    provider = StubListingProvider([_snapshot()], rejected=("bad record",))

    with pytest.raises(IncompleteListingHistoryError, match="nothing is stored"):
        await _service(db_session, provider, tmp_path).ingest_ticker_snapshot("META", D(2020, 1, 2))
    db_session.commit()

    assert db_session.query(ListingHistoryFact).count() == 0
    files = [str(p) for p in (tmp_path / "raw").rglob("*") if p.is_file()]
    assert any("ticker-snapshots-rejected" in p for p in files)
    assert not any("/ticker-snapshots/" in p for p in files)


@pytest.mark.anyio
async def test_unsupported_instrument_types_are_skipped_not_failed(
    db_session: Session, world: World, tmp_path: Path
) -> None:
    world.security("Meta", FIGI_A)
    db_session.commit()

    outcome = await _service(
        db_session, StubListingProvider([_snapshot()], unsupported=2), tmp_path
    ).ingest_ticker_snapshot("META", D(2020, 1, 2))

    assert outcome.records_skipped == 2 and outcome.records_failed == 0


@pytest.mark.anyio
async def test_a_provider_failure_keeps_a_durable_failed_run(
    db_session: Session, world: World, tmp_path: Path
) -> None:
    world.security("Meta", FIGI_A)
    db_session.commit()
    service = _service(db_session, StubListingProvider(error=ProviderServerError(503)), tmp_path)

    with pytest.raises(ProviderServerError):
        await service.ingest_ticker_snapshot("META", D(2020, 1, 2))
    db_session.commit()

    assert db_session.query(IngestionRun).one().status is IngestionStatus.FAILED
    assert db_session.query(ListingHistoryFact).count() == 0


@pytest.mark.anyio
async def test_ticker_events_store_started_facts_anchored_by_the_share_class_figi(
    db_session: Session, world: World, tmp_path: Path
) -> None:
    meta = world.security("Meta", FIGI_A)
    db_session.commit()
    provider = StubListingProvider(
        [_snapshot(as_of_date=D(2026, 10, 2))],
        changes=[("FB", D(2012, 5, 18)), ("META", D(2022, 6, 9))],
    )

    await _service(db_session, provider, tmp_path).ingest_ticker_events(
        "META", anchor_date=D(2026, 10, 2)
    )
    db_session.commit()

    started = {
        (f.ticker, f.effective_date)
        for f in db_session.query(ListingHistoryFact).filter_by(kind=STARTED)
    }
    assert started == {("FB", D(2012, 5, 18)), ("META", D(2022, 6, 9))}
    assert {f.security_id for f in db_session.query(ListingHistoryFact)} == {meta.id}
    assert (
        HistoricalListingResolver(db_session).resolve_security_on(meta.id, D(2018, 3, 1)).ticker
        == "FB"
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("provider", "message"),
    [
        (
            StubListingProvider(
                [_snapshot()], changes=[("FB", D(2012, 5, 18)), ("OTHER", D(2022, 6, 9))]
            ),
            "contradicts the ticker",
        ),
        (StubListingProvider([], changes=[("META", D(2022, 6, 9))]), "expected exactly one"),
        (
            StubListingProvider(
                [_snapshot()], changes=[("META", D(2022, 6, 9))], event_rejected=("odd",)
            ),
            "not storable",
        ),
    ],
)
async def test_ticker_events_are_all_or_nothing(
    db_session: Session, world: World, tmp_path: Path, provider: StubListingProvider, message: str
) -> None:
    world.security("Meta", FIGI_A)
    db_session.commit()

    with pytest.raises(IncompleteListingHistoryError, match=message):
        await _service(db_session, provider, tmp_path).ingest_ticker_events(
            "META", anchor_date=D(2026, 10, 2)
        )
    db_session.commit()

    assert db_session.query(ListingHistoryFact).count() == 0
    assert db_session.query(IngestionRun).one().status is IngestionStatus.FAILED


# ── consumers: one resolver, one answer ─────────────────────────────────────


def _earnings_event(company: Company, source: DataSource, on: date) -> EarningsEvent:
    return EarningsEvent(
        company_id=company.id,
        fiscal_year=on.year,
        fiscal_quarter=1,
        event_date=on,
        timing=EarningsTiming.BEFORE_MARKET,
        reported_eps=Decimal("1.0"),
        estimated_eps=Decimal("0.9"),
        source_id=source.id,
    )


def test_research_alignment_resolves_an_event_before_the_first_observed_date_through_facts(
    db_session: Session, world: World
) -> None:
    meta = _meta(world)
    company = db_session.get(Company, meta.company_id)
    assert company is not None
    event = _earnings_event(company, _source(db_session), D(2024, 5, 1))
    db_session.add(event)
    db_session.commit()

    aligned = align_earnings_event(db_session, event, mode=ResearchMode.DESCRIPTIVE)

    assert aligned.listing_resolution.status is ListingResolutionStatus.RESOLVED
    assert aligned.listing_resolution.evidence is Evidence.SOURCE_STATED_HISTORY


def test_alignment_still_fails_safely_without_history_and_never_uses_the_current_ticker(
    db_session: Session, world: World
) -> None:
    meta = world.security("Meta", FIGI_A)
    world.listing(meta, "META")
    db_session.commit()
    company = db_session.get(Company, meta.company_id)
    assert company is not None

    resolved = resolve_company_listing_for_date(
        db_session, company_id=company.id, on_date=D(2018, 3, 1)
    )

    assert resolved.status is ListingResolutionStatus.NO_LISTING_FOR_DATE


def test_a_rename_era_with_no_stored_listing_cannot_be_aligned_to_price_bars(
    db_session: Session, world: World
) -> None:
    meta = _meta(world)

    resolved = resolve_company_listing_for_date(
        db_session, company_id=meta.company_id, on_date=D(2018, 3, 1)
    )

    assert resolved.status is ListingResolutionStatus.NO_LISTING_FOR_DATE
    assert "no stored listing carries that ticker" in resolved.reason


def test_conflicting_history_surfaces_as_its_own_alignment_status(
    db_session: Session, world: World
) -> None:
    sec = world.security("Messy", FIGI_A)
    world.fact(sec, STARTED, "AAA", D(2020, 1, 2), source="massive")
    world.fact(sec, STARTED, "BBB", D(2020, 1, 2), source="other-vendor")
    db_session.commit()

    resolved = resolve_company_listing_for_date(
        db_session, company_id=sec.company_id, on_date=D(2020, 1, 2)
    )

    assert resolved.status is ListingResolutionStatus.CONFLICTING_HISTORY


def test_earnings_ingestion_attributes_old_events_by_ticker_and_date_through_facts(
    db_session: Session, world: World
) -> None:
    meta = _meta(world)

    assert resolve_company_id(db_session, ticker="FB", event_date=D(2018, 3, 1)) == meta.company_id
    assert (
        resolve_company_id(db_session, ticker="META", event_date=D(2024, 3, 1)) == meta.company_id
    )
    with pytest.raises(UnknownIssuerError):
        resolve_company_id(db_session, ticker="FB", event_date=D(2024, 3, 1))
    with pytest.raises(UnknownIssuerError):
        resolve_company_id(db_session, ticker="META", event_date=D(2018, 3, 1))


def test_earnings_ingestion_refuses_a_conflicted_date(db_session: Session, world: World) -> None:
    sec = world.security("Messy", FIGI_A)
    world.fact(sec, STARTED, "AAA", D(2020, 1, 2), source="massive")
    world.fact(sec, STARTED, "BBB", D(2020, 1, 2), source="other-vendor")
    db_session.commit()

    with pytest.raises(UnknownIssuerError, match="contradictory"):
        resolve_company_id(db_session, ticker="AAA", event_date=D(2020, 1, 2))


@pytest.fixture
def api(db_session: Session) -> TestClient:
    application = create_app(Settings(_env_file=None))
    application.dependency_overrides[get_session] = lambda: db_session
    return TestClient(application)


def test_the_earnings_api_uses_the_same_resolver_for_the_flag_the_filter_and_the_proven_ticker(
    db_session: Session, world: World, api: TestClient
) -> None:
    meta = _meta(world)
    company = db_session.get(Company, meta.company_id)
    assert company is not None
    source = _source(db_session)
    db_session.add_all(
        [
            _earnings_event(company, source, D(2018, 3, 1)),
            _earnings_event(company, source, D(2024, 3, 1)),
        ]
    )
    db_session.commit()

    page = api.get("/api/v1/earnings-events").json()
    by_date = {item["event_date"]: item for item in page["items"]}

    old, new = by_date["2018-03-01"], by_date["2024-03-01"]
    assert old["resolved_tickers"] == ["FB"] and new["resolved_tickers"] == ["META"]
    assert [x["valid_on_event_date"] for x in old["listings"]] == [False], "no FB listing row"
    assert [x["valid_on_event_date"] for x in new["listings"]] == [True]
    assert api.get("/api/v1/earnings-events", params={"ticker": "FB"}).json()["total"] == 1
    assert api.get("/api/v1/earnings-events", params={"ticker": "META"}).json()["total"] == 1
    fb = api.get("/api/v1/earnings-events", params={"ticker": "fb"}).json()["items"][0]
    assert fb["event_date"] == "2018-03-01", "FB matches only the FB-era event"


def test_listing_history_never_changes_point_in_time_eligibility(
    db_session: Session, world: World, api: TestClient
) -> None:
    meta = _meta(world)
    company = db_session.get(Company, meta.company_id)
    assert company is not None
    db_session.add(_earnings_event(company, _source(db_session), D(2024, 3, 1)))
    db_session.commit()

    availability = api.get("/api/v1/earnings-events").json()["items"][0]["availability"]

    assert availability["pit_eligibility"] == "INELIGIBLE_NO_AVAILABILITY_EVIDENCE"
    assert {c.name for c in ListingHistoryFact.__table__.columns}.isdisjoint(
        {"observed_at", "observed_at_basis"}
    )


# ── split ingestion consumes the same evidence ──────────────────────────────


class _SplitStub:
    name = "massive"

    def __init__(self, splits: list[CanonicalStockSplit]) -> None:
        self._splits = splits

    async def get_stock_splits(self, ticker: str) -> SplitsResult:
        del ticker
        capture = RawCapture(
            provider="massive",
            dataset="stock-splits",
            endpoint="/stocks/v1/splits",
            request_parameters={"ticker": "NVDA"},
            body=b'{"status":"OK","results":[]}',
            retrieved_at=NOW,
        )
        return SplitsResult(splits=self._splits, captures=(capture,))


def _splits(session: Session, tmp_path: Path) -> SplitIngestionService:
    return SplitIngestionService(
        session=session,
        provider=_SplitStub([]),
        store=RawResponseStore(root=tmp_path / "raw"),
        now=NOW,
    )


@pytest.mark.anyio
async def test_sourced_history_makes_an_old_era_eligible_for_a_split_fetch_and_coverage(
    db_session: Session, world: World, tmp_path: Path
) -> None:
    nvda = world.security("Nvidia", FIGI_A)
    world.listing(nvda, "NVDA")
    db_session.commit()
    service = _splits(db_session, tmp_path)
    with pytest.raises(UnknownSplitTickerError):
        await service.ingest_stock_splits(
            "NVDA", D(2020, 1, 1), D(2020, 12, 31), provider_history_start=None
        )

    world.fact(nvda, STARTED, "NVDA", D(1999, 1, 22))
    db_session.commit()
    await service.ingest_stock_splits(
        "NVDA", D(2020, 1, 1), D(2020, 12, 31), provider_history_start=None
    )
    db_session.commit()

    coverage = db_session.query(StockSplitCoverage).one()
    assert (coverage.covered_from, coverage.covered_to) == (D(2020, 1, 1), D(2020, 12, 31))


@pytest.mark.anyio
async def test_a_sparse_snapshot_covers_only_its_own_day(
    db_session: Session, world: World, tmp_path: Path
) -> None:
    nvda = world.security("Nvidia", FIGI_A)
    world.listing(nvda, "NVDA")
    world.fact(nvda, OBSERVED, "NVDA", D(2020, 6, 1))
    db_session.commit()

    await _splits(db_session, tmp_path).ingest_stock_splits(
        "NVDA", D(2020, 6, 1), D(2020, 6, 1), provider_history_start=None
    )
    db_session.commit()
    assert db_session.query(StockSplitCoverage).one().covered_to == D(2020, 6, 1)

    with pytest.raises(UnknownSplitTickerError):
        await _splits(db_session, tmp_path).ingest_stock_splits(
            "NVDA", D(2020, 7, 1), D(2020, 7, 31), provider_history_start=None
        )


@pytest.mark.anyio
async def test_a_gap_in_proven_history_is_not_covered_by_a_split_fetch(
    db_session: Session, world: World, tmp_path: Path
) -> None:
    sec = world.security("Gappy", FIGI_A)
    world.fact(sec, STARTED, "GAPP", D(2010, 1, 4))
    world.fact(sec, LAST, "GAPP", D(2015, 3, 31))
    world.fact(sec, STARTED, "GAPP", D(2015, 6, 1))
    world.listing(sec, "GAPP")
    db_session.commit()

    await _splits(db_session, tmp_path).ingest_stock_splits(
        "GAPP", D(2015, 1, 1), D(2015, 12, 31), provider_history_start=None
    )
    db_session.commit()

    ranges = sorted((c.covered_from, c.covered_to) for c in db_session.query(StockSplitCoverage))
    assert ranges == [(D(2015, 1, 1), D(2015, 3, 31)), (D(2015, 6, 1), D(2015, 12, 31))]


@pytest.mark.anyio
async def test_split_ingestion_refuses_a_reused_ticker_range_and_a_conflicted_one(
    db_session: Session, world: World, tmp_path: Path
) -> None:
    _reused(world)
    with pytest.raises(ReusedTickerRangeError):
        await _splits(db_session, tmp_path).ingest_stock_splits(
            "ABC", D(2010, 1, 1), D(2020, 12, 31), provider_history_start=None
        )

    sec = world.security("Messy", "BBG001SCTQY6")
    world.fact(sec, STARTED, "MSY", D(2020, 1, 2), source="massive")
    world.fact(sec, STARTED, "MSZ", D(2020, 1, 2), source="other-vendor")
    world.listing(sec, "MSY")
    db_session.commit()
    with pytest.raises(ListingHistoryConflictError):
        await _splits(db_session, tmp_path).ingest_stock_splits(
            "MSY", D(2020, 1, 1), D(2020, 1, 31), provider_history_start=None
        )


@pytest.mark.anyio
async def test_historical_listing_facts_do_not_unlock_coverage_through_a_retired_symbol(
    db_session: Session, world: World, tmp_path: Path
) -> None:
    _meta(world)

    with pytest.raises(RetiredTickerCoverageError):
        await _splits(db_session, tmp_path).ingest_stock_splits(
            "FB", D(2018, 1, 1), D(2018, 12, 31), provider_history_start=None
        )
    assert db_session.query(StockSplitCoverage).count() == 0


@pytest.mark.anyio
async def test_all_consumers_agree_on_the_same_fact_set_and_date(
    db_session: Session, world: World, api: TestClient, tmp_path: Path
) -> None:
    nvda = world.security("Nvidia", FIGI_A, cik="0001045810")
    world.listing(nvda, "NVDA")
    world.fact(nvda, STARTED, "NVDA", D(1999, 1, 22))
    company = db_session.get(Company, nvda.company_id)
    assert company is not None
    db_session.add(_earnings_event(company, _source(db_session), D(2020, 5, 21)))
    db_session.commit()

    alignment = resolve_company_listing_for_date(
        db_session, company_id=company.id, on_date=D(2020, 5, 21)
    )
    ingestion_company = resolve_company_id(db_session, ticker="NVDA", event_date=D(2020, 5, 21))
    item = api.get("/api/v1/earnings-events", params={"ticker": "NVDA"}).json()["items"][0]
    eras = HistoricalListingResolver(db_session).resolve_ticker_eras(
        "NVDA", D(2020, 5, 1), D(2020, 5, 31)
    )

    assert alignment.status is ListingResolutionStatus.RESOLVED
    assert alignment.security_id == eras.security_id == nvda.id
    assert ingestion_company == company.id
    assert (
        item["resolved_tickers"] == ["NVDA"] and item["listings"][0]["valid_on_event_date"] is True
    )
    assert eras.status is ResolutionStatus.RESOLVED


# ── provenance and determinism ──────────────────────────────────────────────


def test_re_resolving_over_unchanged_facts_gives_the_same_answer_whatever_the_row_order(
    db_session: Session, world: World
) -> None:
    meta = _meta(world)
    resolver = HistoricalListingResolver(db_session)
    first = [
        resolver.resolve_security_on(meta.id, D(2018, 3, 1)),
        resolver.resolve_security_on(meta.id, D(2024, 3, 1)),
    ]

    db_session.execute(
        sa.text("UPDATE listing_history_fact SET created_at = created_at + interval '1 day'")
    )
    db_session.commit()

    again = [
        resolver.resolve_security_on(meta.id, D(2018, 3, 1)),
        resolver.resolve_security_on(meta.id, D(2024, 3, 1)),
    ]
    assert again == first


def test_the_fact_table_forbids_non_canonical_values(db_session: Session, world: World) -> None:
    sec = world.security("Meta", FIGI_A)
    db_session.commit()
    for kwargs in (
        {"ticker": "meta"},
        {"ticker": ""},
        {"kind": STARTED, "mic": "XNAS"},
    ):
        fields: dict[str, object] = {
            "security_id": sec.id,
            "source_id": _source(db_session).id,
            "kind": OBSERVED,
            "ticker": "META",
            "effective_date": D(2020, 1, 2),
        }
        fields.update(kwargs)
        with pytest.raises(sa.exc.IntegrityError), db_session.begin_nested():
            db_session.add(ListingHistoryFact(**fields))
            db_session.flush()


def test_json_payload_helpers_are_not_needed_for_the_resolver() -> None:
    assert json.dumps({"ok": StockSplitAdjustmentType.FORWARD_SPLIT.value})


# ── review fixes: venue evidence, conflict relevance, API consistency, anchors ──────────────────


def test_a_sourced_only_resolution_never_reports_a_venue_it_cannot_evidence(
    db_session: Session, world: World
) -> None:
    sec = world.security("Venue", FIGI_A)
    world.listing(sec, "VNU", FIRST_OBSERVED, mic="XNAS")
    world.fact(sec, STARTED, "VNU", D(2005, 1, 3))
    db_session.commit()
    resolver = HistoricalListingResolver(db_session)

    old = resolver.resolve_security_on(sec.id, D(2015, 6, 1))
    new = resolver.resolve_security_on(sec.id, FIRST_OBSERVED)

    assert old.status is ResolutionStatus.RESOLVED and old.listing_id is not None
    assert old.exchange_mic is None and "venue" in old.reason
    assert new.exchange_mic == "XNAS", "a first-observed interval does cover this date"


def test_an_unrelated_conflict_on_another_security_does_not_block_a_ticker_query(
    db_session: Session, world: World
) -> None:
    old_holder = world.security("Old", FIGI_A)
    new_holder = world.security("New", FIGI_B)
    world.fact(old_holder, STARTED, "QQQ", D(2010, 1, 4))
    world.fact(old_holder, LAST, "QQQ", D(2015, 12, 31))
    world.fact(old_holder, STARTED, "ZZA", D(2023, 3, 10), source="massive")
    world.fact(old_holder, STARTED, "ZZB", D(2023, 3, 10), source="other-vendor")
    world.fact(new_holder, STARTED, "QQQ", D(2020, 1, 2))
    world.listing(new_holder, "QQQ")
    db_session.commit()

    outcome = HistoricalListingResolver(db_session).resolve_ticker_on("QQQ", D(2023, 3, 10))

    assert outcome.status is ResolutionStatus.RESOLVED and outcome.security_id == new_holder.id


def test_the_api_flags_only_the_listing_the_resolver_would_choose(
    db_session: Session, world: World, api: TestClient
) -> None:
    sec = world.security("Moved", FIGI_A)
    world.listing(sec, "ABC", D(2010, 1, 1), D(2020, 1, 1), primary=False)
    world.listing(sec, "ABC", D(2024, 1, 1), mic="ARCX", primary=True)
    world.fact(sec, STARTED, "ABC", D(2005, 1, 3))
    company = db_session.get(Company, sec.company_id)
    assert company is not None
    db_session.add(_earnings_event(company, _source(db_session), D(2015, 6, 1)))
    db_session.commit()

    listings = api.get("/api/v1/earnings-events").json()["items"][0]["listings"]
    flags = {x["valid_from"]: x["valid_on_event_date"] for x in listings}

    assert flags == {"2010-01-01": True, "2024-01-01": False}


def test_ticker_ranges_are_merged_so_the_api_filter_does_not_grow_with_observations(
    db_session: Session, world: World
) -> None:
    sec = world.security("Daily", FIGI_A)
    world.fact(sec, STARTED, "DLY", D(2020, 1, 1))
    for day in range(2, 30):
        world.fact(sec, OBSERVED, "DLY", D(2020, 1, day))
    world.listing(sec, "DLY")
    db_session.commit()

    ranges = HistoricalListingResolver(db_session).ticker_company_ranges("DLY")

    assert len(ranges) == 1, "29 observations, one era and one provisional listing: one range"
    assert (ranges[0][1], ranges[0][2]) == (D(2020, 1, 1), None)


@pytest.mark.anyio
@pytest.mark.parametrize("anchor", [D(2026, 9, 1), D(2026, 10, 4), D(2005, 6, 1)])
async def test_the_events_identity_anchor_must_be_a_recent_past_date(
    db_session: Session, world: World, tmp_path: Path, anchor: date
) -> None:
    world.security("Meta", FIGI_A)
    db_session.commit()
    provider = StubListingProvider(
        [_snapshot(as_of_date=anchor)], changes=[("META", D(2022, 6, 9))]
    )

    with pytest.raises(InvalidAnchorDateError, match="recent snapshot"):
        await _service(db_session, provider, tmp_path).ingest_ticker_events(
            "META", anchor_date=anchor
        )
    assert db_session.query(IngestionRun).count() == 0
    assert db_session.query(ListingHistoryFact).count() == 0


@pytest.mark.anyio
async def test_an_inactive_anchor_snapshot_is_refused(
    db_session: Session, world: World, tmp_path: Path
) -> None:
    world.security("Meta", FIGI_A)
    db_session.commit()
    provider = StubListingProvider(
        [_snapshot(as_of_date=D(2026, 10, 2), active=False)], changes=[("META", D(2022, 6, 9))]
    )

    with pytest.raises(IncompleteListingHistoryError, match="not active"):
        await _service(db_session, provider, tmp_path).ingest_ticker_events(
            "META", anchor_date=D(2026, 10, 2)
        )


def test_a_ticker_claimed_by_two_companies_is_excluded_from_the_api_ranges_like_the_resolver(
    db_session: Session, world: World
) -> None:
    first = world.security("First Co", FIGI_A)
    second = world.security("Second Co", FIGI_B)
    world.fact(first, STARTED, "SHR", D(2020, 1, 1))
    world.fact(first, LAST, "SHR", D(2024, 12, 31))
    world.fact(second, STARTED, "SHR", D(2023, 1, 1))
    world.fact(second, LAST, "SHR", D(2026, 12, 31))
    db_session.commit()
    resolver = HistoricalListingResolver(db_session)

    assert resolver.resolve_ticker_on("SHR", D(2023, 6, 1)).status is ResolutionStatus.AMBIGUOUS
    ranges = resolver.ticker_company_ranges("SHR")

    def claimed(day: date) -> set[uuid.UUID]:
        return {c for c, start, end in ranges if start <= day and (end is None or day < end)}

    assert claimed(D(2021, 6, 1)) == {first.company_id}
    assert claimed(D(2023, 6, 1)) == set(), "ambiguous dates match no event"
    assert claimed(D(2025, 6, 1)) == {second.company_id}


@pytest.mark.anyio
async def test_an_unattributable_anchor_is_counted_as_a_failure(
    db_session: Session, world: World, tmp_path: Path
) -> None:
    world.security("Meta", FIGI_A)
    db_session.commit()
    provider = StubListingProvider(
        [_snapshot(as_of_date=D(2026, 10, 2), share_class_figi=None)],
        changes=[("META", D(2022, 6, 9))],
    )

    with pytest.raises(IncompleteListingHistoryError):
        await _service(db_session, provider, tmp_path).ingest_ticker_events(
            "META", anchor_date=D(2026, 10, 2)
        )
    db_session.commit()

    assert db_session.query(IngestionRun).one().records_failed == 1


def test_a_conflict_about_the_ticker_blocks_every_claimant_in_the_api_ranges_too(
    db_session: Session, world: World
) -> None:
    disputed = world.security("Disputed Co", FIGI_A)
    steady = world.security("Steady Co", FIGI_B)
    world.fact(disputed, STARTED, "SHR", D(2023, 6, 1), source="massive")
    world.fact(disputed, STARTED, "OTH", D(2023, 6, 1), source="other-vendor")
    world.fact(disputed, LAST, "SHR", D(2023, 12, 31), source="massive")
    world.fact(steady, STARTED, "SHR", D(2020, 1, 1))
    world.fact(steady, LAST, "SHR", D(2026, 1, 1))
    db_session.commit()
    resolver = HistoricalListingResolver(db_session)

    assert resolver.resolve_ticker_on("SHR", D(2023, 6, 1)).status is ResolutionStatus.CONFLICT
    ranges = resolver.ticker_company_ranges("SHR")

    def claimed(day: date) -> set[uuid.UUID]:
        return {c for c, start, end in ranges if start <= day and (end is None or day < end)}

    assert claimed(D(2023, 6, 1)) == set(), (
        "the filter agrees with the resolver on the conflicted date"
    )
    assert claimed(D(2022, 6, 1)) == {steady.company_id}


def test_split_eras_refuse_a_range_when_a_conflict_on_another_security_concerns_the_ticker(
    db_session: Session, world: World
) -> None:
    holder = world.security("Holder", FIGI_A)
    world.fact(holder, STARTED, "SHR", D(2020, 1, 1))
    world.listing(holder, "SHR")
    stray = world.security("Stray", FIGI_B)
    world.fact(stray, STARTED, "UUU", D(2020, 1, 4), source="massive")
    world.fact(stray, STARTED, "VVV", D(2020, 1, 4), source="other-vendor")
    world.fact(stray, OBSERVED, "SHR", D(2020, 1, 4))
    db_session.commit()
    resolver = HistoricalListingResolver(db_session)

    assert resolver.resolve_ticker_on("SHR", D(2020, 1, 4)).status is ResolutionStatus.CONFLICT
    assert resolver.resolve_ticker_eras("SHR", D(2020, 1, 1), D(2020, 1, 10)).status is (
        ResolutionStatus.CONFLICT
    )


# ── final-review additions ──────────────────────────────────────────────────


def test_every_derived_boundary_is_exact_around_a_rename_and_a_last_traded_date(
    db_session: Session, world: World
) -> None:
    sec = world.security("Edges", FIGI_A)
    world.fact(sec, STARTED, "OLD", D(2020, 3, 2))
    world.fact(sec, STARTED, "NEW", D(2021, 6, 5))  # a Saturday: the date stays as supplied
    world.fact(sec, LAST, "NEW", D(2022, 12, 31))
    db_session.commit()
    resolver = HistoricalListingResolver(db_session)

    def ticker(day: date) -> str | None:
        outcome = resolver.resolve_security_on(sec.id, day)
        return outcome.ticker if outcome.status is ResolutionStatus.RESOLVED else None

    assert ticker(D(2020, 3, 1)) is None, "the day before the start"
    assert ticker(D(2020, 3, 2)) == "OLD", "start is inclusive"
    assert ticker(D(2021, 6, 4)) == "OLD", "the day before the next start"
    assert ticker(D(2021, 6, 5)) == "NEW", "a non-trading start date is not shifted to a session"
    assert ticker(D(2022, 12, 31)) == "NEW", "the last traded date is still valid"
    assert ticker(D(2023, 1, 1)) is None, "the day after the last traded date"


def test_the_fact_table_rejects_nonsense_dates(db_session: Session, world: World) -> None:
    sec = world.security("Dates", FIGI_A)
    db_session.commit()
    for bad in (D(1800, 1, 1), D(9999, 12, 31)):
        with pytest.raises(sa.exc.IntegrityError), db_session.begin_nested():
            db_session.add(
                ListingHistoryFact(
                    security_id=sec.id,
                    source_id=_source(db_session).id,
                    kind=OBSERVED,
                    ticker="DAT",
                    effective_date=bad,
                )
            )
            db_session.flush()


def test_canonical_snapshots_and_events_reject_out_of_range_dates() -> None:
    with pytest.raises(ValueError, match="outside"):
        CanonicalTickerChange("META", D(9999, 12, 31))
    with pytest.raises(ValueError, match="outside"):
        _snapshot(as_of_date=D(1800, 1, 1))


def test_one_figi_literal_cannot_name_two_securities_under_either_level_label(
    db_session: Session, world: World
) -> None:
    world.security("First", FIGI_A)
    other = world.security("Second", None)
    db_session.commit()

    for level in (IdentifierType.FIGI, IdentifierType.FIGI_SHARE_CLASS):
        with pytest.raises(sa.exc.IntegrityError), db_session.begin_nested():
            db_session.add(
                SecurityIdentifier(
                    security_id=other.id, identifier_type=level, identifier_value=FIGI_A
                )
            )
            db_session.flush()


@pytest.mark.anyio
async def test_events_for_the_current_holder_never_attach_to_a_previous_holder(
    db_session: Session, world: World, tmp_path: Path
) -> None:
    old = world.security("OldCo", FIGI_A)
    new = world.security("NewCo", FIGI_B)
    world.fact(old, STARTED, "XYZ", D(2001, 1, 2))
    world.fact(old, LAST, "XYZ", D(2010, 12, 31))
    db_session.commit()
    provider = StubListingProvider(
        [_snapshot(as_of_date=D(2026, 10, 2), ticker="XYZ", share_class_figi=FIGI_B, cik=None)],
        changes=[("XYZ", D(2015, 3, 2))],
    )

    await _service(db_session, provider, tmp_path).ingest_ticker_events(
        "XYZ", anchor_date=D(2026, 10, 2)
    )
    db_session.commit()

    by_security = {f.security_id for f in db_session.query(ListingHistoryFact)}
    assert by_security == {old.id, new.id}
    assert {
        f.security_id
        for f in db_session.query(ListingHistoryFact).filter(
            ListingHistoryFact.effective_date >= D(2015, 1, 1)
        )
    } == {new.id}
    resolver = HistoricalListingResolver(db_session)
    assert resolver.resolve_ticker_on("XYZ", D(2005, 6, 1)).security_id == old.id
    assert resolver.resolve_ticker_on("XYZ", D(2020, 6, 1)).security_id == new.id


@pytest.mark.anyio
async def test_events_that_would_overlap_a_previous_holders_proven_era_are_refused(
    db_session: Session, world: World, tmp_path: Path
) -> None:
    old = world.security("OldCo", FIGI_A)
    world.security("NewCo", FIGI_B)
    world.fact(old, STARTED, "XYZ", D(2001, 1, 2))
    world.fact(old, LAST, "XYZ", D(2010, 12, 31))
    db_session.commit()
    before = db_session.query(ListingHistoryFact).count()
    provider = StubListingProvider(
        [_snapshot(as_of_date=D(2026, 10, 2), ticker="XYZ", share_class_figi=FIGI_B, cik=None)],
        changes=[("XYZ", D(2005, 3, 2))],
    )

    with pytest.raises(IncompleteListingHistoryError, match="identity cannot be established"):
        await _service(db_session, provider, tmp_path).ingest_ticker_events(
            "XYZ", anchor_date=D(2026, 10, 2)
        )
    db_session.commit()

    assert db_session.query(ListingHistoryFact).count() == before
    assert db_session.query(IngestionRun).one().status is IngestionStatus.FAILED


@pytest.mark.anyio
async def test_a_snapshot_for_a_ticker_another_security_proves_that_day_is_refused(
    db_session: Session, world: World, tmp_path: Path
) -> None:
    holder = world.security("Holder", FIGI_A)
    world.security("Claimant", FIGI_B)
    world.fact(holder, STARTED, "XYZ", D(2001, 1, 2))
    world.fact(holder, LAST, "XYZ", D(2030, 12, 31))
    db_session.commit()
    provider = StubListingProvider([_snapshot(ticker="XYZ", share_class_figi=FIGI_B, cik=None)])

    with pytest.raises(IncompleteListingHistoryError, match="identity cannot be established"):
        await _service(db_session, provider, tmp_path).ingest_ticker_snapshot("XYZ", D(2020, 1, 2))
    db_session.commit()
    assert db_session.query(ListingHistoryFact).filter_by(kind=OBSERVED).count() == 0


@pytest.mark.anyio
async def test_a_later_reuse_of_a_superseded_ticker_does_not_block_re_ingesting_the_earlier_holder(
    db_session: Session, world: World, tmp_path: Path
) -> None:
    first = world.security("First", FIGI_A)
    reuser = world.security("Reuser", FIGI_B)
    world.fact(reuser, STARTED, "OLDT", D(2022, 1, 3))
    world.fact(reuser, LAST, "OLDT", D(2030, 12, 31))
    db_session.commit()
    provider = StubListingProvider(
        [_snapshot(as_of_date=D(2026, 10, 2), ticker="NEWT", share_class_figi=FIGI_A, cik=None)],
        changes=[("OLDT", D(2010, 1, 4)), ("NEWT", D(2020, 1, 2))],
    )

    await _service(db_session, provider, tmp_path).ingest_ticker_events(
        "NEWT", anchor_date=D(2026, 10, 2)
    )
    db_session.commit()

    assert (
        HistoricalListingResolver(db_session).resolve_ticker_on("OLDT", D(2015, 1, 5)).security_id
        == first.id
    )


@pytest.mark.anyio
async def test_an_events_run_with_no_changes_still_verifies_the_anchor_observation(
    db_session: Session, world: World, tmp_path: Path
) -> None:
    holder = world.security("Holder", FIGI_A)
    world.security("Claimant", FIGI_B)
    world.fact(holder, STARTED, "XYZ", D(2001, 1, 2))
    world.fact(holder, LAST, "XYZ", D(2030, 12, 31))
    db_session.commit()
    provider = StubListingProvider(
        [_snapshot(as_of_date=D(2026, 10, 2), ticker="XYZ", share_class_figi=FIGI_B, cik=None)],
        changes=[],
    )

    with pytest.raises(IncompleteListingHistoryError, match="identity cannot be established"):
        await _service(db_session, provider, tmp_path).ingest_ticker_events(
            "XYZ", anchor_date=D(2026, 10, 2)
        )
    db_session.commit()
    assert db_session.query(ListingHistoryFact).filter_by(kind=OBSERVED).count() == 0


@pytest.mark.anyio
async def test_a_ticker_used_in_two_eras_is_verified_per_era_not_across_the_gap(
    db_session: Session, world: World, tmp_path: Path
) -> None:
    world.security("Mover", FIGI_A)
    middle = world.security("Middle Holder", FIGI_B)
    world.fact(middle, STARTED, "AAA", D(2012, 1, 3))
    world.fact(middle, LAST, "AAA", D(2014, 12, 31))
    db_session.commit()
    provider = StubListingProvider(
        [_snapshot(as_of_date=D(2026, 10, 2), ticker="AAA", share_class_figi=FIGI_A, cik=None)],
        changes=[("AAA", D(2010, 1, 4)), ("BBB", D(2011, 1, 3)), ("AAA", D(2020, 1, 2))],
    )

    await _service(db_session, provider, tmp_path).ingest_ticker_events(
        "AAA", anchor_date=D(2026, 10, 2)
    )
    db_session.commit()

    assert db_session.query(IngestionRun).one().status is IngestionStatus.SUCCEEDED
