"""Ingest sourced listing-history FACTS: raw capture, canonical validation, idempotent write.

The pipeline is deliberately split:

    provider -> raw evidence -> canonical record -> sourced FACT (this module)
                                                      |
                                       (nothing is written to ``Listing`` here)
                                                      v
                          derived, on demand, by ``quantforge.identity`` (pure, re-runnable)

A response never mutates ``Listing`` and never stores an interval. Each record becomes one
single-date fact attached to a SECURITY resolved through its **share-class FIGI** (a ticker, a
composite FIGI or a CIK never identifies a security). A malformed, unattributable or contradictory
record fails the whole run -- the run row stays durable, no fact survives -- so a bad response can
never partially rewrite history.

Snapshot semantics: a dated snapshot stores ``TICKER_OBSERVED`` for THAT date only (and
``LAST_TRADED`` when the provider states a last-traded date). The date is the REQUEST's ``date``
parameter, which Massive documents as the point in time the ticker was available; the snapshot's
primary exchange is not documented as of that date, so the fact's ``mic`` is left NULL. Ticker
events store ``TICKER_STARTED`` (inclusive start of the new symbol). The experimental events
endpoint carries no identifier, so identity is anchored by a dated snapshot fetched in the same
run: the share-class FIGI of that snapshot decides which security the events belong to, and the
provider's own latest event must agree with the ticker asked about. No exchange continuity is
implied by anything here.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from quantforge.identity.listing_history import ResolutionStatus
from quantforge.identity.resolver import HistoricalListingResolver
from quantforge.ingestion.canonical import CanonicalTickerChange, CanonicalTickerSnapshot
from quantforge.ingestion.raw_store import RawResponseStore
from quantforge.ingestion.runs import FAILED_RUN_RECORDED, RunOutcome, RunRecorder
from quantforge.ingestion.service import store_rejected_captures
from quantforge.ingestion.sources import get_or_create_data_source
from quantforge.models.company import Company
from quantforge.models.enums import ListingFactKind, ProviderType
from quantforge.models.ingestion import IngestionRun
from quantforge.models.listing_history import ListingHistoryFact
from quantforge.models.security import Security
from quantforge.providers.errors import ProviderError
from quantforge.providers.listing_history import (
    ListingHistoryProvider,
    TickerEventsResult,
    TickerSnapshotsResult,
)

__all__ = [
    "ANCHOR_MAX_AGE_DAYS",
    "IncompleteListingHistoryError",
    "InvalidAnchorDateError",
    "ListingFactConflictError",
    "ListingHistoryIngestionService",
]

logger = logging.getLogger(__name__)


class IncompleteListingHistoryError(RuntimeError):
    """A record was rejected, unattributable or inconsistent; nothing from the run is stored."""


class InvalidAnchorDateError(ValueError):
    """The identity-anchoring snapshot date is not a recent past date (pre-run check)."""


#: The events endpoint describes the entity that holds the ticker NOW, so its identity anchor must
#: be a snapshot from now-ish: an old anchor could name a previous holder of a reused ticker.
ANCHOR_MAX_AGE_DAYS = 7


class ListingFactConflictError(RuntimeError):
    """A stored fact contradicts an incoming fact with the same identity.

    No path raises it today: a fact's whole content is its identity (source, security, kind,
    ticker, date) now that a snapshot's venue is no longer stored, so an identical incoming fact is
    simply skipped. Kept as the CLI-mapped failure type for any future stated attribute.
    """


class _FactWriter:
    __slots__ = ("_now", "_run_id", "_session")

    def __init__(self, session: Session, *, now: datetime, run: IngestionRun) -> None:
        if sa.inspect(run).identity is None:
            message = "run must be flushed before the writer can stamp it on rows"
            raise ValueError(message)
        self._session = session
        self._now = now
        self._run_id = run.id

    def write(
        self,
        *,
        security_id: uuid.UUID,
        source_id: uuid.UUID,
        kind: ListingFactKind,
        ticker: str,
        effective_date: date,
    ) -> bool:
        """Insert the fact, or skip an identical one; return whether a row was written.

        ``mic`` is never written (it stays NULL): a snapshot's primary exchange is not documented
        as of the requested date (``SNAPSHOT_ATTRIBUTES_ARE_AS_OF_DATE`` in the Massive mapper), so
        storing it would turn an undated attribute into a dated claim.
        """
        inserted = self._session.execute(
            postgresql.insert(ListingHistoryFact)
            .values(
                id=uuid.uuid4(),
                security_id=security_id,
                source_id=source_id,
                kind=kind.value,
                ticker=ticker,
                effective_date=effective_date,
                ingestion_run_id=self._run_id,
                ingested_at=self._now,
                created_at=self._now,
            )
            .on_conflict_do_nothing(
                index_elements=["source_id", "security_id", "kind", "ticker", "effective_date"]
            )
            .returning(ListingHistoryFact.id)
        ).scalar_one_or_none()
        return inserted is not None


class ListingHistoryIngestionService:
    """Stores sourced listing-history facts for one ticker at a time."""

    __slots__ = ("_now", "_provider", "_resolver", "_session", "_store")

    def __init__(
        self,
        *,
        session: Session,
        provider: ListingHistoryProvider,
        store: RawResponseStore,
        now: datetime,
    ) -> None:
        self._session = session
        self._provider = provider
        self._store = store
        self._now = now
        self._resolver = HistoricalListingResolver(session)

    def _attribute(self, snapshot: CanonicalTickerSnapshot) -> uuid.UUID:
        """The stored security this snapshot is about, via its share-class FIGI only."""
        if snapshot.share_class_figi is None:
            message = (
                f"{snapshot.ticker} snapshot has no share-class FIGI, so it cannot be attributed "
                "to a security (a ticker or composite FIGI is not an identity)"
            )
            raise IncompleteListingHistoryError(message)
        security_id = self._resolver.security_id_for_share_class_figi(snapshot.share_class_figi)
        if security_id is None:
            message = (
                f"no stored security has share-class FIGI {snapshot.share_class_figi} "
                f"({snapshot.ticker}); ingest its reference first"
            )
            raise IncompleteListingHistoryError(message)
        if snapshot.cik is not None:
            company_cik = self._session.scalar(
                sa.select(Company.cik)
                .join(Security, Security.company_id == Company.id)
                .where(Security.id == security_id)
            )
            if company_cik is not None and company_cik != snapshot.cik:
                message = (
                    f"{snapshot.ticker}: provider CIK {snapshot.cik} contradicts the stored "
                    f"company CIK {company_cik} for this share class; refusing to attribute"
                )
                raise IncompleteListingHistoryError(message)
        return security_id

    def _verify_identity(
        self,
        security_id: uuid.UUID,
        checks: dict[str, list[tuple[date, date]]],
        run: RunRecorder,
    ) -> None:
        """Refuse facts that, once written, leave the identity ambiguous or contradicted.

        A recent anchor snapshot ties the events to a security, but it cannot by itself prove the
        events do not cross a reused ticker, a relisting or a conflicting boundary. So after the
        facts are written (inside the run's savepoint) the shared resolver must still resolve every
        ticker involved, over the dates the new facts claim, to THIS security with no contradiction
        touching it; otherwise the run fails and nothing survives.
        """
        self._session.flush()
        history = self._resolver.histories([security_id])[security_id]
        for ticker, first, last in (
            (ticker, first, last) for ticker, ranges in checks.items() for first, last in ranges
        ):
            outcome = self._resolver.resolve_ticker_eras(ticker, first, last)
            conflict = history.conflict_in(first, last)
            if (
                outcome.status is not ResolutionStatus.RESOLVED
                or outcome.security_id != security_id
            ):
                reason = f"{ticker} {first}..{last}: {outcome.status.value} -- {outcome.reason}"
            elif conflict is not None:
                reason = f"{ticker} {first}..{last}: {conflict.reason}"
            else:
                continue
            run.count_failed(1)
            message = (
                f"the new facts would leave {reason}; identity cannot be established, so "
                "nothing is stored"
            )
            raise IncompleteListingHistoryError(message)

    def _write_snapshot(
        self,
        writer: _FactWriter,
        snapshot: CanonicalTickerSnapshot,
        *,
        security_id: uuid.UUID,
        source_id: uuid.UUID,
    ) -> int:
        written = 0
        if snapshot.active:
            written += writer.write(
                security_id=security_id,
                source_id=source_id,
                kind=ListingFactKind.TICKER_OBSERVED,
                ticker=snapshot.ticker,
                effective_date=snapshot.as_of_date,
            )
        if snapshot.delisted_date is not None:
            written += writer.write(
                security_id=security_id,
                source_id=source_id,
                kind=ListingFactKind.LAST_TRADED,
                ticker=snapshot.ticker,
                effective_date=snapshot.delisted_date,
            )
        return written

    async def ingest_ticker_snapshot(
        self, ticker: str, on_date: date, *, active: bool = True
    ) -> RunOutcome:
        """Fetch a dated snapshot of ``ticker`` and store its facts (one date, nothing more)."""
        symbol = ticker.strip().upper()
        self._session.info.pop(FAILED_RUN_RECORDED, None)
        with RunRecorder(
            session=self._session,
            store=self._store,
            provider=self._provider.name,
            dataset="ticker-snapshots",
            request_parameters={"ticker": symbol, "date": on_date.isoformat(), "active": active},
            now=self._now,
        ) as run:
            source = get_or_create_data_source(
                self._session,
                name=self._provider.name,
                provider_type=ProviderType.MARKET_DATA_VENDOR,
            )
            try:
                result = await self._provider.get_ticker_snapshots(symbol, on_date, active=active)
            except ProviderError as exc:
                store_rejected_captures(run, self._store, exc.captures)
                raise
            if result.rejected:
                run.count_received(len(result.snapshots) + len(result.rejected))
                run.count_failed(len(result.rejected))
                store_rejected_captures(run, self._store, result.captures)
                message = (
                    f"{len(result.rejected)} snapshot record(s) for {symbol} were rejected; "
                    f"nothing is stored; first: {result.rejected[0]}"
                )
                raise IncompleteListingHistoryError(message)
            if result.captures:
                run.record_captures(self._store.store_all(result.captures))
            writer = _FactWriter(self._session, now=self._now, run=run.row)
            written = skipped = 0
            for snapshot in result.snapshots:
                try:
                    security_id = self._attribute(snapshot)
                except IncompleteListingHistoryError:
                    run.count_received(len(result.snapshots) + result.unsupported)
                    run.count_failed(1)
                    raise
                stored = self._write_snapshot(
                    writer, snapshot, security_id=security_id, source_id=source.id
                )
                written += stored
                skipped += 0 if stored else 1
                if snapshot.active:
                    day = snapshot.as_of_date
                    self._verify_identity(security_id, {snapshot.ticker: [(day, day)]}, run)
            run.count_received(len(result.snapshots) + result.unsupported)
            run.count_written(written)
            run.count_skipped(skipped + result.unsupported)
            outcome = run.succeed(self._now)
            logger.info("ticker snapshot %s %s: %s", symbol, on_date, outcome)
            return outcome

    def _validated_anchor_and_changes(
        self,
        run: RunRecorder,
        symbol: str,
        anchor_date: date,
        anchor: TickerSnapshotsResult,
        events: TickerEventsResult,
    ) -> tuple[CanonicalTickerSnapshot, uuid.UUID, tuple[CanonicalTickerChange, ...]]:
        """The anchoring snapshot, its security and the events -- or an explicit failure."""
        assert events.events is not None  # the caller rejected a missing events payload
        if len(anchor.snapshots) != 1:
            run.count_failed(1)
            message = (
                f"{symbol}: the identity-anchoring snapshot on {anchor_date} returned "
                f"{len(anchor.snapshots)} supported record(s), expected exactly one"
            )
            raise IncompleteListingHistoryError(message)
        snapshot = anchor.snapshots[0]
        if not snapshot.active:
            run.count_failed(1)
            message = f"{symbol}: the anchoring snapshot on {anchor_date} is not active"
            raise IncompleteListingHistoryError(message)
        try:
            security_id = self._attribute(snapshot)
        except IncompleteListingHistoryError:
            run.count_failed(1)
            raise
        changes = events.events.changes
        if changes and changes[-1].ticker != symbol:
            run.count_failed(1)
            message = (
                f"{symbol}: the provider's latest ticker event is {changes[-1].ticker}, which "
                "contradicts the ticker the events were requested for"
            )
            raise IncompleteListingHistoryError(message)
        return snapshot, security_id, changes

    async def ingest_ticker_events(self, ticker: str, *, anchor_date: date) -> RunOutcome:
        """Fetch ticker-change events for the entity now holding ``ticker`` and store them as facts.

        ``anchor_date`` is the date of the identity-anchoring snapshot (a recent date on which the
        ticker traded). Everything is stored, or nothing.
        """
        symbol = ticker.strip().upper()
        today = self._now.astimezone(UTC).date()
        if anchor_date > today or (today - anchor_date).days > ANCHOR_MAX_AGE_DAYS:
            message = (
                f"anchor date {anchor_date} is not within the last {ANCHOR_MAX_AGE_DAYS} days "
                f"(today is {today} UTC): the events describe whoever holds {symbol} NOW, so the "
                "identity anchor must be a recent snapshot, not one from a previous holder's era"
            )
            raise InvalidAnchorDateError(message)
        self._session.info.pop(FAILED_RUN_RECORDED, None)
        with RunRecorder(
            session=self._session,
            store=self._store,
            provider=self._provider.name,
            dataset="ticker-events",
            request_parameters={"ticker": symbol, "anchor_date": anchor_date.isoformat()},
            now=self._now,
        ) as run:
            source = get_or_create_data_source(
                self._session,
                name=self._provider.name,
                provider_type=ProviderType.MARKET_DATA_VENDOR,
            )
            try:
                anchor = await self._provider.get_ticker_snapshots(symbol, anchor_date)
                events = await self._provider.get_ticker_events(symbol)
            except ProviderError as exc:
                store_rejected_captures(run, self._store, exc.captures)
                raise
            captures = (*anchor.captures, *events.captures)
            problems = (*anchor.rejected, *events.rejected)
            if problems or events.events is None:
                run.count_received(
                    len(anchor.snapshots)
                    + (0 if events.events is None else len(events.events.changes))
                    + len(problems)
                )
                run.count_failed(max(1, len(problems)))
                store_rejected_captures(run, self._store, captures)
                first = problems[0] if problems else "no events were returned"
                message = f"ticker history for {symbol} is not storable: {first}"
                raise IncompleteListingHistoryError(message)
            run.record_captures(self._store.store_all(captures))
            # Counts: ``received`` is provider records; ``written``/``skipped`` count FACTS (one
            # snapshot record can state two).
            run.count_received(len(anchor.snapshots) + len(events.events.changes))
            snapshot, security_id, changes = self._validated_anchor_and_changes(
                run, symbol, anchor_date, anchor, events
            )
            writer = _FactWriter(self._session, now=self._now, run=run.row)
            written = skipped = 0
            stored = self._write_snapshot(
                writer, snapshot, security_id=security_id, source_id=source.id
            )
            written += stored
            skipped += 0 if stored else 1
            for change in changes:
                wrote = writer.write(
                    security_id=security_id,
                    source_id=source.id,
                    kind=ListingFactKind.TICKER_STARTED,
                    ticker=change.ticker,
                    effective_date=change.effective_date,
                )
                written += wrote
                skipped += 0 if wrote else 1
            # Each ticker is verified only over ITS OWN era(s) -- from its start to the next change
            # (or the anchor for the current one) -- so a later, unrelated reuse of a superseded
            # ticker cannot make the result depend on ingestion order. The anchor's own observation
            # is always verified, even when the provider returned no events.
            checks: dict[str, list[tuple[date, date]]] = {symbol: [(anchor_date, anchor_date)]}
            for index, change in enumerate(changes):
                era_end = (
                    changes[index + 1].effective_date if index + 1 < len(changes) else anchor_date
                )
                checks.setdefault(change.ticker, []).append((change.effective_date, era_end))
            self._verify_identity(security_id, checks, run)
            run.count_written(written)
            run.count_skipped(skipped)
            outcome = run.succeed(self._now)
            logger.info("ticker events %s: %s", symbol, outcome)
            return outcome
