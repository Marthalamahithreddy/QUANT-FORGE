"""Orchestration: one provider call, one run row, one transaction.

What this layer is responsible for
----------------------------------
Everything between "the adapter fetched bytes" and "the rows are committed", and
nothing else. The mapping lives in the adapter, the storage rules live in
:mod:`~quantforge.ingestion.daily` and
:mod:`~quantforge.ingestion.reference`, and this composes them:

1. make sure the provider exists as a ``DataSource`` (every observation needs one);
2. open an ``IngestionRun`` *before* the request, so a crash mid-flight is recorded;
3. call the provider and write its raw bytes, untouched, before interpreting them;
4. map and resolve;
5. write, idempotently;
6. finalise the run in the same transaction as the data.

Step 3 before step 4 is the ordering that matters. If interpretation fails, the
evidence is already on disk, so the failure can be investigated against what the
provider actually sent instead of being reproduced from memory. The reverse order
would throw the bytes away on exactly the failures you most want to debug.

One transaction
---------------
The run row and the data it describes commit together, deliberately. Committing
the run first would allow a "succeeded" run whose rows were lost; committing the
rows first would allow rows no run accounts for. Neither is recoverable without
a repair job, and this milestone does not have one.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from quantforge.ingestion.benchmark import (
    BENCHMARK_VENUE,
    BenchmarkBarConflictError,
    BenchmarkBarIngestor,
    get_or_create_benchmark,
)
from quantforge.ingestion.canonical import CanonicalEquityReference, ResolvedVenue
from quantforge.ingestion.daily import DailyBarIngestor, ProviderDataConflict
from quantforge.ingestion.earnings import EarningsIngestor
from quantforge.ingestion.raw_store import RawResponseStore
from quantforge.ingestion.reference import (
    ReferenceResolutionError,
    ReferenceResolver,
)
from quantforge.ingestion.runs import RunOutcome, RunRecorder
from quantforge.ingestion.sources import get_or_create_data_source
from quantforge.models.security import Exchange, Listing
from quantforge.providers.base import MarketDataProvider, RawCapture
from quantforge.providers.earnings import EarningsProvider
from quantforge.providers.massive.dto import MassiveTickerDetailsDTO
from quantforge.providers.massive.mapper import to_canonical_reference

__all__ = ["EarningsIngestionService", "IngestionService", "UnknownListingError"]

logger = logging.getLogger(__name__)


class UnknownListingError(LookupError):
    """The ticker is not stored under a single unambiguous current listing."""


@dataclass(frozen=True, slots=True)
class ReferenceOutcome:
    """What one reference produced, for the CLI to print."""

    ticker: str
    created: bool
    matched_on: str
    company_id: str
    security_id: str
    listing_id: str


class IngestionService:
    """Runs reference and daily-bar ingestion against one provider."""

    __slots__ = ("_now", "_provider", "_session", "_store")

    def __init__(
        self,
        *,
        session: Session,
        provider: MarketDataProvider,
        store: RawResponseStore,
        now: datetime,
    ) -> None:
        self._session = session
        self._provider = provider
        self._store = store
        self._now = now

    # ── Reference ──────────────────────────────────────────────────────────

    async def ingest_reference(
        self, tickers: Sequence[str]
    ) -> tuple[RunOutcome, list[ReferenceOutcome]]:
        """Resolve and store reference data for each ticker.

        One run covers the whole batch, because a batch is one operational act:
        "refresh these symbols". A per-ticker run would make the run table
        unreadable for a 500-symbol refresh, and would lose the single answer to
        "did this refresh work?".

        A ticker the provider does not know is not a failure. It is counted and
        reported, and the run still succeeds -- "no such symbol" is a legitimate
        answer, not an error. A malformed record *is* counted as failed, and makes
        the run ``partial``.
        """
        source = get_or_create_data_source(
            self._session,
            name=self._provider.name,
            base_url=None,
        )
        outcomes: list[ReferenceOutcome] = []
        resolved = 0
        written = 0
        failed = 0

        with RunRecorder(
            session=self._session,
            store=self._store,
            provider=self._provider.name,
            dataset="reference",
            request_parameters={"tickers": list(tickers)},
            now=self._now,
        ) as run:
            resolver = ReferenceResolver(self._session)
            captures: list[RawCapture] = []

            for ticker in tickers:
                try:
                    result = await self._provider.get_ticker_details(ticker)
                except ReferenceResolutionError:
                    raise
                except Exception as exc:
                    # One symbol's failure must not abandon the other 499. It is
                    # counted, logged with the symbol, and the run finishes
                    # `partial` so the shortfall is visible on the run row.
                    failed += 1
                    logger.warning("reference lookup failed for %s: %s", ticker, exc)
                    continue

                captures.extend(result.captures)
                if result.reference is None:
                    logger.info("provider has no ticker %s", ticker)
                    continue

                try:
                    reference = self._to_canonical(result.reference, ticker)
                except Exception as exc:
                    failed += 1
                    logger.warning("reference payload unusable for %s: %s", ticker, exc)
                    continue

                try:
                    resolution = resolver.resolve(
                        reference,
                        source_id=source.id,
                        as_of=self._today(),
                    )
                except ReferenceResolutionError as exc:
                    failed += 1
                    logger.warning("cannot resolve %s safely: %s", ticker, exc)
                    continue

                resolved += 1
                if resolution.created:
                    written += 1
                outcomes.append(
                    ReferenceOutcome(
                        ticker=reference.ticker,
                        created=resolution.created,
                        matched_on=resolution.matched_on,
                        company_id=str(resolution.company_id),
                        security_id=str(resolution.security_id),
                        listing_id=str(resolution.listing_id),
                    )
                )

            run.count_received(len(tickers))
            # Only rows actually inserted count as written. A symbol that
            # resolved to an existing issuer/security/listing (a match) wrote
            # nothing, and is counted as skipped alongside provider-unknown
            # tickers, so a re-run of an already-ingested batch is a no-op on
            # every counter that quantifies writes.
            run.count_written(written)
            run.count_skipped((resolved - written) + (len(tickers) - resolved - failed))
            run.count_failed(failed)
            self._record(run, captures)

            outcome = run.partial(self._now) if failed else run.succeed(self._now)
        return outcome, outcomes

    def _to_canonical(self, view: Any, ticker: str) -> CanonicalEquityReference:
        """Map a provider reference view to the canonical form.

        Typed as the provider's own DTO because this service currently only
        supports the Massive adapter, whose contract is checked at construction.
        Asserting the type here means a second adapter has to extend this method
        explicitly rather than being silently fed the wrong shape.
        """
        if not isinstance(view, MassiveTickerDetailsDTO):
            message = (
                f"{self._provider.name} returned {type(view).__name__}, which this "
                "service does not know how to map. Add a branch here for the new "
                "adapter rather than casting it to Massive's DTO."
            )
            raise TypeError(message)
        return to_canonical_reference(view, identifier=ticker)

    # ── Daily bars ─────────────────────────────────────────────────────────

    async def ingest_daily_bars(
        self, ticker: str, start: date, end: date, *, exchange_mic: str | None = None
    ) -> RunOutcome:
        """Fetch and store daily bars for one already-resolved listing.

        The listing must already exist, because a bar's session date is derived
        through the venue's timezone and the bars endpoint carries no exchange
        code. Requiring reference ingestion first is what makes that conversion
        possible without a second request or a hard-coded fallback venue.
        """
        source = get_or_create_data_source(self._session, name=self._provider.name)
        listing, venue = self._find_listing(ticker, exchange_mic=exchange_mic)

        with RunRecorder(
            session=self._session,
            store=self._store,
            provider=self._provider.name,
            dataset="daily-bars",
            request_parameters={
                "ticker": ticker.upper(),
                "start": start.isoformat(),
                "end": end.isoformat(),
                "exchange": venue.mic,
            },
            now=self._now,
        ) as run:
            result = await self._provider.get_daily_bars(ticker, start, end, venue=venue)
            self._record(run, result.captures)

            ingestor = DailyBarIngestor(self._session, now=self._now, run=run.row)
            try:
                counts = ingestor.ingest(
                    result.bars,
                    listing_id=listing.id,
                    source_id=source.id,
                    ticker=ticker.upper(),
                    run=run.row,
                )
            except ProviderDataConflict as exc:
                # The run is marked failed by the context manager's __exit__ as it
                # propagates, which records the conflict message on the row. The
                # exception is re-raised: a source revising history is something
                # to decide about, not something to swallow.
                logger.warning("bar conflict during %s ingestion: %s", ticker, exc)
                raise

            outcome = run.partial(self._now) if counts.failed else run.succeed(self._now)
            logger.info("daily bars for %s: %s", ticker.upper(), outcome)
            return outcome

    # ── Benchmark bars ─────────────────────────────────────────────────────

    async def ingest_benchmark_daily_bars(
        self,
        symbol: str,
        start: date,
        end: date,
        *,
        name: str,
        currency: str | None = None,
    ) -> RunOutcome:
        """Fetch and store daily levels for a benchmark instrument (e.g. an ETF proxy).

        Reuses the same Massive daily-aggregate endpoint, adapter, canonical
        mapping, raw-capture and run machinery as :meth:`ingest_daily_bars` --
        only the persistence target differs:
        :class:`~quantforge.models.benchmark.BenchmarkPriceBar` via
        :class:`~quantforge.ingestion.benchmark.BenchmarkBarIngestor`, not
        ``DailyPriceBar`` via a resolved ``Listing``. A benchmark has no
        ``Listing``/``Exchange`` to resolve, so
        :data:`~quantforge.ingestion.benchmark.BENCHMARK_VENUE` stands in
        purely for the timezone conversion ``get_daily_bars`` needs to turn a
        bar's instant into a session date; it is never stored.

        Args:
            symbol: The provider ticker to fetch, and the value stored as
                ``Benchmark.symbol`` (e.g. ``"SPY"``).
            start: Inclusive start of the date range.
            end: Inclusive end of the date range.
            name: The benchmark's full, explicit identity (e.g. ``"SPY (ETF
                proxy for the S&P 500)"``). Required, never defaulted or
                inferred -- see
                :func:`quantforge.ingestion.benchmark.get_or_create_benchmark`
                on why benchmark selection stays explicit all the way down to
                ingestion.
            currency: Recorded only if this call creates the ``Benchmark`` row.
        """
        symbol = symbol.upper()
        source = get_or_create_data_source(self._session, name=self._provider.name)
        benchmark = get_or_create_benchmark(
            self._session,
            name=name,
            symbol=symbol,
            currency=currency,
            source_id=source.id,
        )

        with RunRecorder(
            session=self._session,
            store=self._store,
            provider=self._provider.name,
            dataset="benchmark-daily-bars",
            request_parameters={
                "symbol": symbol,
                "start": start.isoformat(),
                "end": end.isoformat(),
            },
            now=self._now,
        ) as run:
            result = await self._provider.get_daily_bars(symbol, start, end, venue=BENCHMARK_VENUE)
            self._record(run, result.captures)

            ingestor = BenchmarkBarIngestor(self._session, now=self._now)
            try:
                counts = ingestor.ingest(
                    result.bars,
                    benchmark_id=benchmark.id,
                    source_id=source.id,
                    symbol=symbol,
                    run=run.row,
                )
            except BenchmarkBarConflictError as exc:
                logger.warning("benchmark level conflict during %s ingestion: %s", symbol, exc)
                raise

            outcome = run.partial(self._now) if counts.failed else run.succeed(self._now)
            logger.info("benchmark levels for %s: %s", symbol, outcome)
            return outcome

    def _find_listing(
        self, ticker: str, *, exchange_mic: str | None
    ) -> tuple[Listing, ResolvedVenue]:
        """Locate the one current listing this ticker means.

        A ticker is unique only within a venue, so a symbol listed on two
        exchanges is genuinely ambiguous. Rather than picking one -- which would
        attach another venue's prices to the wrong instrument, and put them on the
        wrong session dates because the timezones differ -- the ambiguity is
        reported along with the candidates, and the caller chooses with
        ``--exchange``.
        """
        conditions = [Listing.ticker == ticker.upper(), Listing.valid_to.is_(None)]
        if exchange_mic is not None:
            mic = exchange_mic.strip().upper()
            venue_id = self._session.scalar(sa.select(Exchange.id).where(Exchange.mic == mic))
            if venue_id is None:
                message = f"no stored exchange has MIC {mic}"
                raise UnknownListingError(message)
            conditions.append(Listing.exchange_id == venue_id)

        listings = list(self._session.scalars(sa.select(Listing).where(*conditions)))
        if not listings:
            # No hint when the venue table is empty: nothing can be ingested yet
            # and the message below is already complete.
            known = self._session.scalar(sa.select(Exchange.mic).limit(1))
            hint = (
                f" Ingest its reference first: quantforge ingestion reference {ticker.upper()}"
                if known is None
                else ""
            )
            message = f"{ticker.upper()} is not stored.{hint}"
            raise UnknownListingError(message)

        if len(listings) > 1:
            candidates = sorted(self._mic_for(li.exchange_id) for li in listings)
            message = (
                f"{ticker.upper()} is currently listed on {len(listings)} venues "
                f"({', '.join(candidates)}); pass --exchange MIC to say which one "
                "this ingestion is about"
            )
            raise UnknownListingError(message)

        listing = listings[0]
        return listing, self._venue_for(listing.exchange_id)

    def _mic_for(self, exchange_id: object) -> str:
        mic = self._session.scalar(sa.select(Exchange.mic).where(Exchange.id == exchange_id))
        return str(mic) if mic is not None else "?"

    def _venue_for(self, exchange_id: object) -> ResolvedVenue:
        """Rebuild the venue from the stored exchange row.

        Read back from storage rather than remembered from reference ingestion, so
        the timezone that decides a bar's session date is the one actually on the
        row the bar will reference. A venue's stored zone is the single authority;
        deriving it twice from two code paths is how they drift.
        """
        exchange = self._session.get(Exchange, exchange_id)
        if exchange is None:  # pragma: no cover - the FK guarantees it exists
            message = f"listing points at exchange {exchange_id}, which is gone"
            raise UnknownListingError(message)
        return ResolvedVenue(mic=exchange.mic, name=exchange.name, timezone_name=exchange.timezone)

    # ── Shared ─────────────────────────────────────────────────────────────

    def _record(self, run: RunRecorder, captures: Sequence[RawCapture]) -> None:
        """Persist the raw responses and point the run at the manifest."""
        _record_captures(run, self._store, captures)

    def _today(self) -> date:
        return self._now.astimezone(UTC).date()


def _record_captures(
    run: RunRecorder, store: RawResponseStore, captures: Sequence[RawCapture]
) -> None:
    """Persist raw responses and point the run at their manifest.

    Shared by every ingestion service so the "store the bytes before
    interpreting them" ordering is one line, not a copy-pasted fragment.
    """
    if not captures:
        return
    run.record_captures(store.store_all(captures))


class EarningsIngestionService:
    """Runs earnings ingestion against an :class:`EarningsProvider`.

    The earnings counterpart of :class:`IngestionService`: reserves the run
    before the request, stores the raw bytes before interpreting them, resolves
    each event's ticker to a company, writes idempotently, and finalises the
    run -- all in the caller's transaction, so the run row and the events it
    describes commit together.
    """

    __slots__ = ("_now", "_provider", "_session", "_store")

    def __init__(
        self,
        *,
        session: Session,
        provider: EarningsProvider,
        store: RawResponseStore,
        now: datetime,
    ) -> None:
        self._session = session
        self._provider = provider
        self._store = store
        self._now = now

    async def ingest_earnings(self, ticker: str, start: date, end: date) -> RunOutcome:
        """Fetch and store earnings observations for one ticker over a range.

        The ticker does not need to be a stored listing: each event is resolved
        to a company through its own event date, so a range spanning a ticker
        reuse still attributes every event to the issuer that actually reported
        it. Unresolvable events are counted as failed and the run finishes
        ``partial``; the valid ones still commit. A provider failure propagates
        and the run is recorded as ``failed``.

        ``observed_at`` comes from the canonical event only, never from this
        service: about when the source made the fact available, stored as the
        provider stated it, with ``None`` meaning undisclosed. ``ingested_at`` is
        stamped here with the real time of writing, and the two are never
        conflated.
        """
        source = get_or_create_data_source(self._session, name=self._provider.name)
        request_parameters = {
            "ticker": ticker.upper(),
            "start": start.isoformat(),
            "end": end.isoformat(),
        }
        with RunRecorder(
            session=self._session,
            store=self._store,
            provider=self._provider.name,
            dataset="earnings",
            request_parameters=request_parameters,
            now=self._now,
        ) as run:
            result = await self._provider.get_earnings(ticker, start, end)
            _record_captures(run, self._store, result.captures)

            ingestor = EarningsIngestor(self._session, now=self._now, run=run.row)
            counts = ingestor.ingest(
                result.events,
                source_id=source.id,
            )

            outcome = run.partial(self._now) if counts.failed else run.succeed(self._now)
            logger.info("earnings for %s: %s", ticker.upper(), outcome)
            return outcome
