"""Integration tests for earnings ingestion, against real PostgreSQL.

The writer's contract is the mirror image of the daily-bar writer's: write what
is new, skip what is exactly identical, and **preserve** what merely differs.
The last one is the earnings-specific rule -- a changed estimate is a revision
to keep beside the original, not a conflict to refuse -- and it is exactly the
behaviour a schema built on nullable columns and SQLite's NULL-distinct
semantics would get wrong. These tests run against the real ``UniqueConstraint``
and ``TIMESTAMPTZ``/``NUMERIC`` columns to prove the policy holds where it has
to hold: the database.

Rows are located by their distinguishing values rather than by row position
wherever the ordering is not load-bearing: events written in one batch share an
injected ``created_at``, so positional unpacking would depend on PostgreSQL's
arbitrary tie ordering.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from quantforge.ingestion.canonical import (
    CanonicalEarningsEvent,
    CanonicalEquityReference,
    ResolvedVenue,
)
from quantforge.ingestion.daily import IngestCounts
from quantforge.ingestion.earnings import (
    EarningsIngestor,
    UnknownIssuerError,
    earnings_fingerprint,
    resolve_company_id,
)
from quantforge.ingestion.raw_store import RawResponseStore, sha256_hex
from quantforge.ingestion.reference import ReferenceResolver
from quantforge.ingestion.service import EarningsIngestionService
from quantforge.ingestion.sources import get_or_create_data_source
from quantforge.models.company import Company
from quantforge.models.earnings import EarningsEvent
from quantforge.models.enums import AvailabilityBasis, EarningsTiming, IngestionStatus, SecurityType
from quantforge.models.ingestion import IngestionRun
from quantforge.models.security import Exchange, Listing, Security
from quantforge.providers.base import RawCapture
from quantforge.providers.earnings import EarningsResult
from quantforge.providers.errors import ProviderAuthenticationError
from quantforge.providers.finnhub.dto import FinnhubEarningsCalendarEntry
from quantforge.providers.finnhub.mapper import to_canonical_earnings_event
from quantforge.providers.json_decimal import decode_json_document

pytestmark = pytest.mark.integration

NEW_YORK = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
XNAS = ResolvedVenue(mic="XNAS", name="Nasdaq", timezone_name="America/New_York")

RANGE_START = date(2026, 9, 1)
RANGE_END = date(2026, 9, 30)


class StubEarningsProvider:
    """Structural ``EarningsProvider``: returns a canned result."""

    def __init__(self, result: EarningsResult) -> None:
        self._result = result

    @property
    def name(self) -> str:
        return "massive"

    async def get_earnings(self, ticker: str, start: date, end: date) -> EarningsResult:
        del ticker, start, end
        return self._result


class FailingEarningsProvider:
    """Structural ``EarningsProvider`` that always fails authentication."""

    @property
    def name(self) -> str:
        return "massive"

    async def get_earnings(self, ticker: str, start: date, end: date) -> EarningsResult:
        del ticker, start, end
        raise ProviderAuthenticationError(403)


def _capture() -> RawCapture:
    return RawCapture(
        provider="massive",
        dataset="earnings",
        endpoint="/benzinga/v1/earnings/{ticker}",
        request_parameters={"ticker": "AAPL"},
        body=b'{"earnings": []}',
        retrieved_at=NOW,
        provider_status="200",
    )


def _event(**overrides: object) -> CanonicalEarningsEvent:
    fields: dict[str, object] = {
        "ticker": "AAPL",
        "fiscal_year": 2026,
        "fiscal_quarter": 3,
        # Matches the UTC calendar date of the timestamp below (20:30 EDT on
        # the 14th is 00:30 UTC on the 15th), preserving this fixture's
        # existing date-boundary behaviour for the listing-coverage checks
        # that resolve against event_date.
        "event_date": date(2026, 9, 15),
        "event_timestamp": datetime(2026, 9, 14, 20, 30, tzinfo=NEW_YORK),
        "timing": EarningsTiming.AFTER_MARKET,
        "reported_eps": Decimal("2.35"),
        "estimated_eps": Decimal("2.20"),
        "reported_revenue": Decimal("95000000000"),
        "estimated_revenue": Decimal("94000000000"),
        "currency": "USD",
        "observed_at": None,
    }
    fields.update(overrides)
    if fields.get("observed_at") is not None:
        fields.setdefault("observed_at_basis", AvailabilityBasis.SOURCE_PUBLICATION)
    return CanonicalEarningsEvent(**fields)  # type: ignore[arg-type]


@pytest.fixture
def source_id(db_session: Session) -> uuid.UUID:
    row = get_or_create_data_source(db_session, name="massive")
    db_session.commit()
    assert row.id is not None
    return row.id


@pytest.fixture
def alt_source_id(db_session: Session) -> uuid.UUID:
    row = get_or_create_data_source(db_session, name="alt-vendor")
    db_session.commit()
    assert row.id is not None
    return row.id


@pytest.fixture
def resolved(db_session: Session, source_id: uuid.UUID) -> dict[str, uuid.UUID]:
    """A resolved AAPL listing, so there is a real issuer to attach events to."""
    reference = CanonicalEquityReference(
        ticker="AAPL",
        security_type=SecurityType.EQUITY.value,
        venue=XNAS,
        company_name="Apple Inc.",
        cik="0000320193",
        share_class_figi="BBG001S5QY8",
        currency_code="USD",
    )
    result = ReferenceResolver(db_session).resolve(
        reference, source_id=source_id, as_of=RANGE_START
    )
    db_session.commit()
    return {"company_id": result.company_id, "listing_id": result.listing_id}


@pytest.fixture
def xnas_id(db_session: Session) -> uuid.UUID:
    exchange = db_session.scalar(sa.select(Exchange).where(Exchange.mic == "XNAS"))
    assert exchange is not None, "reference fixture should have created XNAS"
    return exchange.id


def _ingest(
    session: Session,
    source: uuid.UUID,
    *events: CanonicalEarningsEvent,
    run: IngestionRun | None = None,
) -> IngestCounts:
    return EarningsIngestor(session, now=NOW, run=run).ingest(events, source_id=source)


def _count(session: Session) -> int:
    return session.scalar(sa.select(sa.func.count()).select_from(EarningsEvent)) or 0


def _by_eps(session: Session, value: Decimal) -> EarningsEvent:
    row = session.scalar(sa.select(EarningsEvent).where(EarningsEvent.reported_eps == value))
    assert row is not None, f"expected a stored event with reported_eps={value}"
    return row


# ── writing ─────────────────────────────────────────────────────────────────


def test_an_unflushed_run_is_refused(db_session: Session) -> None:
    """A run cannot be stamped on rows until its id is real.

    The writer stamps ``ingestion_run_id`` on every row, so the run must have
    been flushed before the ingestor is constructed with it -- otherwise the
    link the provenance story depends on would silently point nowhere.
    """
    unflushed = IngestionRun(
        provider="massive",
        dataset="earnings",
        status=IngestionStatus.RUNNING,
        started_at=NOW,
        request_parameters={},
    )
    with pytest.raises(ValueError, match="run must be flushed"):
        EarningsIngestor(db_session, now=NOW, run=unflushed)


def test_a_new_event_is_written_with_every_fact_exact(
    db_session: Session, source_id: uuid.UUID, resolved: dict[str, uuid.UUID]
) -> None:
    event = _event()
    counts = _ingest(db_session, source_id, event)
    db_session.commit()

    assert (counts.received, counts.written, counts.skipped, counts.failed) == (1, 1, 0, 0)
    row = _by_eps(db_session, Decimal("2.35"))
    assert row.company_id == resolved["company_id"]
    assert row.fiscal_year == event.fiscal_year
    assert row.fiscal_quarter == event.fiscal_quarter
    assert row.event_timestamp == event.event_timestamp
    assert row.timing == EarningsTiming.AFTER_MARKET
    assert row.reported_eps == Decimal("2.3500")
    assert row.estimated_eps == Decimal("2.2000")
    assert row.reported_revenue == Decimal("95000000000.0000")
    assert row.estimated_revenue == Decimal("94000000000.0000")
    assert row.currency == "USD"
    assert row.source_id == source_id
    assert row.observed_at is None
    assert row.ingested_at == NOW
    assert row.fingerprint == earnings_fingerprint(event)


def test_observed_at_is_stored_only_when_the_source_provided_it(
    db_session: Session, source_id: uuid.UUID, resolved: dict[str, uuid.UUID]
) -> None:
    disclosed = datetime(2026, 9, 13, 21, 0, tzinfo=UTC)
    _ingest(db_session, source_id, _event(observed_at=disclosed))
    _ingest(db_session, source_id, _event(reported_eps=Decimal("2.50"), observed_at=None))
    db_session.commit()

    with_disclosure = _by_eps(db_session, Decimal("2.35"))
    without = _by_eps(db_session, Decimal("2.50"))
    assert with_disclosure.observed_at == disclosed
    assert without.observed_at is None


def test_negative_eps_and_exact_decimal_revenue_round_trip(
    db_session: Session, source_id: uuid.UUID, resolved: dict[str, uuid.UUID]
) -> None:
    _ingest(db_session, source_id, _event(reported_eps=Decimal("-1.25")))
    _ingest(db_session, source_id, _event(reported_revenue=Decimal("1234567890.1234")))
    db_session.commit()

    loss = _by_eps(db_session, Decimal("-1.25"))
    big = _by_eps(db_session, Decimal("2.35"))
    assert loss.reported_eps == Decimal("-1.2500")
    assert big.reported_revenue == Decimal("1234567890.1234")


def test_a_date_only_event_persists_with_a_null_timestamp(
    db_session: Session, source_id: uuid.UUID, resolved: dict[str, uuid.UUID]
) -> None:
    """The real, expected shape of a Finnhub-style earnings-calendar
    observation: a calendar date and no exact instant, end to end through
    the actual ingestion pipeline -- not fabricated at any layer."""
    event = _event(event_timestamp=None)
    counts = _ingest(db_session, source_id, event)
    db_session.commit()

    assert (counts.received, counts.written, counts.skipped, counts.failed) == (1, 1, 0, 0)
    row = _by_eps(db_session, Decimal("2.35"))
    assert row.event_timestamp is None
    assert row.event_date == event.event_date


# ── idempotency and revisions ───────────────────────────────────────────────


def test_an_exact_rerun_is_a_noop(
    db_session: Session, source_id: uuid.UUID, resolved: dict[str, uuid.UUID]
) -> None:
    event = _event()
    _ingest(db_session, source_id, event)
    db_session.commit()

    counts = _ingest(db_session, source_id, event)
    db_session.commit()
    assert (counts.received, counts.written, counts.skipped, counts.failed) == (1, 0, 1, 0)
    assert _count(db_session) == 1


def test_an_inline_duplicate_is_written_once(
    db_session: Session, source_id: uuid.UUID, resolved: dict[str, uuid.UUID]
) -> None:
    event = _event()
    counts = _ingest(db_session, source_id, event, event)
    db_session.commit()

    assert (counts.received, counts.written, counts.skipped, counts.failed) == (2, 1, 1, 0)
    assert _count(db_session) == 1


def test_a_date_only_exact_rerun_is_a_noop(
    db_session: Session, source_id: uuid.UUID, resolved: dict[str, uuid.UUID]
) -> None:
    """Idempotency must hold with no ``event_timestamp`` in the fact set at
    all -- the fingerprint's ``event_date`` contribution alone must be enough
    to recognise the re-fetch."""
    event = _event(event_timestamp=None)
    _ingest(db_session, source_id, event)
    db_session.commit()

    counts = _ingest(db_session, source_id, event)
    db_session.commit()
    assert (counts.received, counts.written, counts.skipped, counts.failed) == (1, 0, 1, 0)
    assert _count(db_session) == 1


def test_a_date_only_revision_is_preserved_not_overwritten(
    db_session: Session, source_id: uuid.UUID, resolved: dict[str, uuid.UUID]
) -> None:
    """A changed figure on a date-only observation is still a new revision,
    exactly as it would be if an exact timestamp were present."""
    _ingest(db_session, source_id, _event(event_timestamp=None))
    db_session.commit()
    counts = _ingest(
        db_session, source_id, _event(event_timestamp=None, estimated_eps=Decimal("2.45"))
    )
    db_session.commit()

    assert (counts.received, counts.written, counts.skipped, counts.failed) == (1, 1, 0, 0)
    assert _count(db_session) == 2
    original = _by_eps(db_session, Decimal("2.35"))
    assert original.event_timestamp is None
    assert original.estimated_eps == Decimal("2.2000")


def test_a_changed_estimate_is_a_revision_not_a_conflict(
    db_session: Session, source_id: uuid.UUID, resolved: dict[str, uuid.UUID]
) -> None:
    _ingest(db_session, source_id, _event())
    db_session.commit()

    counts = _ingest(db_session, source_id, _event(estimated_eps=Decimal("2.45")))
    db_session.commit()

    assert (counts.received, counts.written, counts.skipped, counts.failed) == (1, 1, 0, 0)
    assert _count(db_session) == 2
    original = _by_eps(db_session, Decimal("2.35"))
    assert original.estimated_eps == Decimal("2.2000")
    revised = _by_estimated(db_session, Decimal("2.45"))
    assert revised.estimated_eps == Decimal("2.4500")
    assert original.id != revised.id
    assert original.fingerprint != revised.fingerprint
    assert original.company_id == revised.company_id


def test_a_changed_reported_eps_is_preserved(
    db_session: Session, source_id: uuid.UUID, resolved: dict[str, uuid.UUID]
) -> None:
    _ingest(db_session, source_id, _event())
    db_session.commit()
    _ingest(db_session, source_id, _event(reported_eps=Decimal("2.41")))
    db_session.commit()

    assert _count(db_session) == 2
    assert _by_eps(db_session, Decimal("2.35")).reported_eps == Decimal("2.3500")
    assert _by_eps(db_session, Decimal("2.41")).reported_eps == Decimal("2.4100")


def test_a_changed_event_timestamp_is_preserved(
    db_session: Session, source_id: uuid.UUID, resolved: dict[str, uuid.UUID]
) -> None:
    moved = datetime(2026, 9, 15, 7, 0, tzinfo=NEW_YORK)
    _ingest(db_session, source_id, _event())
    db_session.commit()
    _ingest(db_session, source_id, _event(event_timestamp=moved))
    db_session.commit()

    assert _count(db_session) == 2


def test_different_sources_never_collapse(
    db_session: Session, source_id: uuid.UUID, alt_source_id: uuid.UUID
) -> None:
    reference = CanonicalEquityReference(
        ticker="AAPL",
        security_type=SecurityType.EQUITY.value,
        venue=XNAS,
        company_name="Apple Inc.",
        cik="0000320193",
        share_class_figi="BBG001S5QY8",
        currency_code="USD",
    )
    ReferenceResolver(db_session).resolve(reference, source_id=source_id, as_of=RANGE_START)
    db_session.commit()

    event = _event()
    _ingest(db_session, source_id, event)
    _ingest(db_session, alt_source_id, event)
    db_session.commit()

    assert _count(db_session) == 2
    by_source = {row.source_id: row for row in db_session.scalars(sa.select(EarningsEvent)).all()}
    assert by_source[source_id].fingerprint == by_source[alt_source_id].fingerprint
    assert by_source[source_id].id != by_source[alt_source_id].id


# ── resolution ──────────────────────────────────────────────────────────────


def test_resolution_is_date_aware_across_a_reused_ticker(
    db_session: Session,
    source_id: uuid.UUID,
    resolved: dict[str, uuid.UUID],
    xnas_id: uuid.UUID,
) -> None:
    """A relisted symbol must attribute old events to the old issuer."""
    old_company = Company(legal_name="Old Apple Corp", display_name="Old Apple Corp")
    db_session.add(old_company)
    db_session.flush()
    old_security = Security(
        company_id=old_company.id,
        security_type=SecurityType.EQUITY,
        name="Old Apple Common",
        currency="USD",
    )
    db_session.add(old_security)
    db_session.flush()
    db_session.add(
        Listing(
            security_id=old_security.id,
            exchange_id=xnas_id,
            ticker="AAPL",
            valid_from=date(2000, 1, 1),
            valid_to=date(2005, 1, 1),
            is_primary=True,
        )
    )
    db_session.commit()

    current = resolve_company_id(db_session, ticker="AAPL", event_date=date(2026, 9, 14))
    historic = resolve_company_id(db_session, ticker="AAPL", event_date=date(2002, 3, 1))
    assert current == resolved["company_id"]
    assert historic == old_company.id

    with pytest.raises(UnknownIssuerError):
        # 2010 falls in the gap between the historic interval and today.
        resolve_company_id(db_session, ticker="AAPL", event_date=date(2010, 6, 1))


def test_resolution_refuses_a_ticker_covered_by_two_issuers(
    db_session: Session,
    resolved: dict[str, uuid.UUID],
    xnas_id: uuid.UUID,
) -> None:
    """Two issuers both covering ``AAPL`` on one date is not a coin flip.

    The reference resolution never fabricates a company: a reused symbol whose
    historic and current listings overlap is exactly the ambiguity the error
    exists for, and attributing the event to a guessed issuer would taint the
    fact with a lookup guess.
    """
    squatter = Company(legal_name="Squatter Corp", display_name="Squatter Corp")
    db_session.add(squatter)
    db_session.flush()
    squatter_security = Security(
        company_id=squatter.id,
        security_type=SecurityType.EQUITY,
        name="Squatter Common",
        currency="USD",
    )
    db_session.add(squatter_security)
    db_session.flush()
    db_session.add(
        Listing(
            security_id=squatter_security.id,
            exchange_id=xnas_id,
            ticker="AAPL",
            # Overlaps the resolved listing's open interval precisely on the
            # event date the resolver is asked about.
            valid_from=date(2026, 1, 1),
            valid_to=date(2027, 1, 1),
            is_primary=True,
        )
    )
    db_session.commit()

    with pytest.raises(UnknownIssuerError):
        resolve_company_id(db_session, ticker="AAPL", event_date=date(2026, 9, 14))


def test_an_unknown_ticker_is_an_explicit_failure(
    db_session: Session, source_id: uuid.UUID, resolved: dict[str, uuid.UUID]
) -> None:
    counts = _ingest(db_session, source_id, _event(ticker="ZZZZ"))
    db_session.commit()
    assert (counts.received, counts.written, counts.skipped, counts.failed) == (1, 0, 0, 1)
    assert _count(db_session) == 0


# ── service, raw provenance and transactions ────────────────────────────────


@pytest.mark.anyio
async def test_service_writes_events_and_links_run_and_raw_capture(
    db_session: Session,
    source_id: uuid.UUID,
    resolved: dict[str, uuid.UUID],
    tmp_path: Path,
) -> None:
    capture = _capture()
    provider = StubEarningsProvider(EarningsResult(events=[_event()], captures=(capture,)))
    store = RawResponseStore(tmp_path)
    service = EarningsIngestionService(session=db_session, provider=provider, store=store, now=NOW)
    await service.ingest_earnings("AAPL", RANGE_START, RANGE_END)
    db_session.commit()

    run = db_session.scalar(sa.select(IngestionRun).where(IngestionRun.dataset == "earnings"))
    assert run is not None
    assert run.status is IngestionStatus.SUCCEEDED
    assert run.records_written == 1
    assert run.records_skipped == 0
    assert run.records_failed == 0

    row = _by_eps(db_session, Decimal("2.35"))
    assert row.ingestion_run_id == run.id
    assert row.source_id == source_id

    # Raw provenance: the run points at a manifest whose artifact is the exact
    # capture, content-addressed by the hash recorded on the run.
    assert run.raw_artifact_path is not None
    assert run.raw_content_sha256 is not None
    manifest = json.loads(store.read(run.raw_artifact_path).decode("utf-8"))
    entry = manifest["artifacts"][0]
    assert entry["sha256"] == sha256_hex(capture.body)
    assert store.read(entry["path"]) == capture.body
    assert entry["deduplicated"] is False


@pytest.mark.anyio
async def test_service_rerun_is_a_noop_and_the_runs_are_attributable(
    db_session: Session,
    resolved: dict[str, uuid.UUID],
    tmp_path: Path,
) -> None:
    provider = StubEarningsProvider(EarningsResult(events=[_event()], captures=(_capture(),)))
    store = RawResponseStore(tmp_path)
    service = EarningsIngestionService(session=db_session, provider=provider, store=store, now=NOW)

    await service.ingest_earnings("AAPL", RANGE_START, RANGE_END)
    db_session.commit()
    second = await service.ingest_earnings("AAPL", RANGE_START, RANGE_END)
    db_session.commit()

    assert second.records_written == 0
    assert second.records_skipped == 1
    runs = list(db_session.scalars(sa.select(IngestionRun).order_by(IngestionRun.started_at)))
    assert runs[0].records_written == 1
    assert runs[1].records_written == 0
    assert _count(db_session) == 1


# ── Finnhub adapter compatibility (Step 7.2C) ───────────────────────────────


def _finnhub_event(**overrides: object) -> CanonicalEarningsEvent:
    """A real Finnhub DTO, decoded through the real Decimal-safe JSON decoder
    and the real adapter mapper -- not a hand-built canonical event. This is
    what actually proves the new provider is compatible with the existing
    7.3 persistence layer, rather than just asserting it in prose.
    """
    fields: dict[str, object] = {
        "symbol": "AAPL",
        "date": "2026-09-14",
        "quarter": 3,
        "year": 2026,
        "epsActual": 2.35,
        "epsEstimate": 2.20,
        "revenueActual": 95000000000,
        "revenueEstimate": 94000000000,
        "hour": "amc",
    }
    fields.update(overrides)
    if fields.get("observed_at") is not None:
        fields.setdefault("observed_at_basis", AvailabilityBasis.SOURCE_PUBLICATION)
    decoded = decode_json_document(json.dumps(fields).encode())
    assert isinstance(decoded, dict)
    entry = FinnhubEarningsCalendarEntry.model_validate(decoded)
    return to_canonical_earnings_event(entry, identifier="AAPL")


class StubFinnhubProvider:
    """Structural ``EarningsProvider`` identified as Finnhub, returning a
    canned result built from the real adapter's own mapping -- see
    :func:`_finnhub_event`."""

    def __init__(self, result: EarningsResult) -> None:
        self._result = result

    @property
    def name(self) -> str:
        return "finnhub"

    async def get_earnings(self, ticker: str, start: date, end: date) -> EarningsResult:
        del ticker, start, end
        return self._result


@pytest.mark.anyio
async def test_finnhub_shaped_events_persist_through_the_real_service(
    db_session: Session,
    resolved: dict[str, uuid.UUID],
    tmp_path: Path,
) -> None:
    """A date-only, Finnhub-mapped event must flow through
    ``EarningsIngestionService`` exactly like a Massive-mapped one: resolved,
    fingerprinted, written, and linked to its run -- proving the new provider
    needs no change to the existing 7.3 persistence design.
    """
    event = _finnhub_event()
    assert event.event_timestamp is None, "sanity: this is the date-only shape under test"

    provider = StubFinnhubProvider(EarningsResult(events=[event], captures=(_capture(),)))
    store = RawResponseStore(tmp_path)
    service = EarningsIngestionService(session=db_session, provider=provider, store=store, now=NOW)
    outcome = await service.ingest_earnings("AAPL", RANGE_START, RANGE_END)
    db_session.commit()

    assert outcome.records_written == 1
    row = _by_eps(db_session, Decimal("2.35"))
    assert row.event_timestamp is None
    assert row.event_date == date(2026, 9, 14)
    assert row.company_id == resolved["company_id"]

    run = db_session.scalar(sa.select(IngestionRun).where(IngestionRun.dataset == "earnings"))
    assert run is not None
    assert row.ingestion_run_id == run.id


@pytest.mark.anyio
async def test_finnhub_shaped_rerun_through_the_service_is_a_noop(
    db_session: Session,
    resolved: dict[str, uuid.UUID],
    tmp_path: Path,
) -> None:
    """Idempotency must hold end to end through the service for a date-only,
    Finnhub-shaped event too: second run writes nothing, and no duplicate
    ``EarningsEvent`` row is created -- exactly the property Step 13's live
    verification would check against the real API, proven here without one.
    """
    event = _finnhub_event()
    provider = StubFinnhubProvider(EarningsResult(events=[event], captures=(_capture(),)))
    store = RawResponseStore(tmp_path)
    service = EarningsIngestionService(session=db_session, provider=provider, store=store, now=NOW)

    first = await service.ingest_earnings("AAPL", RANGE_START, RANGE_END)
    db_session.commit()
    second = await service.ingest_earnings("AAPL", RANGE_START, RANGE_END)
    db_session.commit()

    assert first.records_written == 1
    assert second.records_written == 0
    assert second.records_skipped == 1
    assert _count(db_session) == 1


@pytest.mark.anyio
async def test_unresolvable_ticker_finalises_the_run_partial(
    db_session: Session,
    resolved: dict[str, uuid.UUID],
    tmp_path: Path,
) -> None:
    provider = StubEarningsProvider(
        EarningsResult(events=[_event(ticker="ZZZZ")], captures=(_capture(),))
    )
    store = RawResponseStore(tmp_path)
    service = EarningsIngestionService(session=db_session, provider=provider, store=store, now=NOW)

    outcome = await service.ingest_earnings("ZZZZ", RANGE_START, RANGE_END)
    db_session.commit()

    assert outcome.status is IngestionStatus.PARTIAL
    assert (outcome.records_received, outcome.records_written, outcome.records_failed) == (
        1,
        0,
        1,
    )
    assert _count(db_session) == 0


@pytest.mark.anyio
async def test_a_provider_failure_records_a_failed_run(
    db_session: Session,
    resolved: dict[str, uuid.UUID],
    tmp_path: Path,
) -> None:
    store = RawResponseStore(tmp_path)
    service = EarningsIngestionService(
        session=db_session,
        provider=FailingEarningsProvider(),
        store=store,
        now=NOW,
    )

    with pytest.raises(ProviderAuthenticationError):
        await service.ingest_earnings("AAPL", RANGE_START, RANGE_END)
    db_session.commit()

    run = db_session.scalar(sa.select(IngestionRun))
    assert run is not None
    assert run.status is IngestionStatus.FAILED
    assert run.error_type == "ProviderAuthenticationError"
    assert "HTTP 403" in (run.error_message or "")
    assert _count(db_session) == 0


@pytest.mark.anyio
async def test_the_service_never_commits_its_own_transaction(
    db_session: Session,
    resolved: dict[str, uuid.UUID],
    tmp_path: Path,
) -> None:
    provider = StubEarningsProvider(EarningsResult(events=[_event()], captures=(_capture(),)))
    store = RawResponseStore(tmp_path)
    service = EarningsIngestionService(session=db_session, provider=provider, store=store, now=NOW)

    await service.ingest_earnings("AAPL", RANGE_START, RANGE_END)
    db_session.rollback()

    assert _count(db_session) == 0
    assert (db_session.scalar(sa.select(sa.func.count()).select_from(IngestionRun)) or 0) == 0


def _by_estimated(db_session: Session, value: Decimal) -> EarningsEvent:
    row = db_session.scalar(sa.select(EarningsEvent).where(EarningsEvent.estimated_eps == value))
    assert row is not None, f"expected a stored event with estimated_eps={value}"
    return row
