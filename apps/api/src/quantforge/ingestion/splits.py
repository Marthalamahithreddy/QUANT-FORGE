"""Ingest historical stock splits: raw capture, canonical validation, idempotent write.

Same standards as the other providers: raw responses are stored before they are
interpreted, a run row is opened first and finalised in the caller's transaction
(a failure rolls back the run's domain writes but keeps a durable ``failed`` run),
and nothing partial survives. Nothing here adjusts a price or a share count.

Identity
--------
A split attaches to a **security**. The provider names it through a ticker, which is
resolved against the stored listings, never against today's ticker: before any run opens,
the ticker must belong to exactly one security over the requested range (a reused ticker is
:class:`ReusedTickerRangeError`; none is :class:`UnknownSplitTickerError`), and the dates
that security actually held it -- half-open ``[valid_from, valid_to)`` -- form its *eras*.
A returned split dated outside the requested range is skipped (the provider returns a
ticker's whole history). One dated INSIDE the range but outside every era means the ticker ->
security mapping is unproven there, so it fails the run (:class:`UnmappedSplitError`) and no
coverage is written; it is never attributed and never covered. Several listings of the same security under one symbol are fine: a split is a
security-level fact.

Coverage
--------
A run records coverage -- "a complete fetch covered these dates for this security" -- only
when it fetched every page, every record validated and every row was stored. Any failure
fails the run and writes no coverage. The covered ranges are the eras within the requested
range. Coverage is recorded only through a CURRENT ticker of the security
(``valid_to IS NULL`` in the stored reference data -- so it depends on that data being
fresh): whether Massive indexes a retired or delisted symbol is unverified, and an empty
answer for one must never become coverage, so a renamed security's pre-rename era and any
delisted security stay "coverage unavailable" until that is verified live. The range must
begin no earlier than the provider plan serves (the caller states it) and end at least
``COVERAGE_SAFETY_MARGIN_DAYS`` before the day of the fetch: a provider's answer about the
future, or about the last few days before every record is published, is not evidence that
nothing happened. Without coverage, an empty table must never be read as "no split".

Conflicts
---------
The identity is ``(source, provider split id)``. Re-ingesting identical facts is a
skip; the same id with different facts raises :class:`StockSplitConflictError` and
overwrites nothing.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from quantforge.ingestion.canonical import CanonicalStockSplit
from quantforge.ingestion.raw_store import RawResponseStore
from quantforge.ingestion.runs import FAILED_RUN_RECORDED, RunOutcome, RunRecorder
from quantforge.ingestion.service import store_rejected_captures
from quantforge.ingestion.sources import get_or_create_data_source
from quantforge.models.corporate_action import StockSplit, StockSplitCoverage
from quantforge.models.enums import ProviderType
from quantforge.models.ingestion import IngestionRun
from quantforge.models.security import Listing
from quantforge.providers.errors import ProviderError
from quantforge.providers.splits import SplitProvider

__all__ = [
    "AmbiguousSplitSecurityError",
    "IncompleteSplitFetchError",
    "InvalidSplitRangeError",
    "RetiredTickerCoverageError",
    "ReusedTickerRangeError",
    "SplitIngestionService",
    "StockSplitConflictError",
    "StockSplitIngestor",
    "UnknownSplitTickerError",
    "UnmappedSplitError",
    "conflicting_split_fields",
]

logger = logging.getLogger(__name__)

_ONE_DAY = timedelta(days=1)

#: Coverage may only claim dates at least this many days before the fetch. A provider's
#: records are not guaranteed to be published the moment a split takes effect, and an
#: effective date is not an availability time, so the most recent days are never claimed.
#: A conservative policy margin chosen here, NOT a provider-stated guarantee.
COVERAGE_SAFETY_MARGIN_DAYS = 7

_COMPARED_FIELDS = ("security_id", "execution_date", "old_shares", "new_shares", "adjustment_type")


class InvalidSplitRangeError(ValueError):
    """The requested range is not a bounded, fully-past, ordered date range."""


class RetiredTickerCoverageError(ValueError):
    """Coverage was requested through a ticker the security no longer (or never last) held.

    Whether Massive indexes a split under the symbol in force on its effective date or
    under the issuer's current symbol is not verified (no live probe has run). Asking for
    a retired symbol could return an empty answer simply because the provider does not
    index it, and an empty answer must never become coverage. Until that is verified,
    coverage is recorded only through the security's latest ticker.
    """


class UnknownSplitTickerError(LookupError):
    """No stored listing carried this ticker anywhere in the requested range."""


class AmbiguousSplitSecurityError(RuntimeError):
    """More than one distinct security resolves; nothing is guessed."""


class ReusedTickerRangeError(AmbiguousSplitSecurityError):
    """The ticker belonged to different securities within the requested range.

    A usage problem -- narrow the range to one holder -- raised before any run opens, so
    it leaves no failed-run row.
    """


class UnmappedSplitError(RuntimeError):
    """A returned split falls inside the requested range but outside every known ticker era.

    The provider says this ticker had a split on a date on which QuantForge's listing history
    cannot show the ticker belonging to the security (before it was listed, in a gap between
    listings, or after it ended). That means the ticker -> security mapping is incomplete or
    inconsistent, so the fetch cannot be called clean and no coverage is recorded. Narrow the
    range to the dates the security's listing history covers, or repair the reference data.
    A record outside the *requested* range is a different, harmless case and is skipped.
    """


class IncompleteSplitFetchError(RuntimeError):
    """A record was rejected, so the fetch cannot be called complete; no coverage is written."""


class StockSplitConflictError(RuntimeError):
    """A stored split disagrees with the incoming facts for the same provider split id."""

    def __init__(self, *, provider_split_id: str, differences: dict[str, tuple[Any, Any]]) -> None:
        self.provider_split_id = provider_split_id
        self.differences = differences
        detail = "; ".join(
            f"{field}: stored={stored!r} incoming={incoming!r}"
            for field, (stored, incoming) in sorted(differences.items())
        )
        super().__init__(
            f"split {provider_split_id}: stored facts differ from the incoming ({detail}). "
            "The existing row is left untouched; decide explicitly which is right."
        )


def conflicting_split_fields(
    stored: StockSplit, incoming: CanonicalStockSplit, security_id: uuid.UUID
) -> dict[str, tuple[Any, Any]]:
    """Fields where the incoming split disagrees with the stored row."""
    incoming_values: dict[str, Any] = {
        "security_id": security_id,
        "execution_date": incoming.execution_date,
        "old_shares": incoming.old_shares,
        "new_shares": incoming.new_shares,
        "adjustment_type": incoming.adjustment_type,
    }
    differences: dict[str, tuple[Any, Any]] = {}
    for name in _COMPARED_FIELDS:
        stored_value = getattr(stored, name)
        if stored_value != incoming_values[name]:
            differences[name] = (stored_value, incoming_values[name])
    return differences


@dataclass(frozen=True, slots=True)
class _Era:
    """A date range, inclusive, over which one security held the queried ticker."""

    start: date
    end: date


def _current_tickers(session: Session, security_id: uuid.UUID) -> set[str]:
    """Tickers of the security's still-open listings (``valid_to IS NULL``).

    A delisted security has none, and so can never get coverage: whether Massive indexes a
    delisted symbol -- or has reused it for another issuer -- is unverified, and an empty
    answer for one must never become coverage.
    """
    return set(
        session.scalars(
            sa.select(Listing.ticker).where(
                Listing.security_id == security_id, Listing.valid_to.is_(None)
            )
        )
    )


def _resolve_eras(
    session: Session, ticker: str, start: date, end: date
) -> tuple[uuid.UUID, list[_Era]]:
    """The one security that held ``ticker`` in ``[start, end]`` and the eras it held it."""
    listings = list(
        session.scalars(
            sa.select(Listing).where(
                Listing.ticker == ticker,
                Listing.valid_from <= end,
                sa.or_(Listing.valid_to.is_(None), Listing.valid_to > start),
            )
        )
    )
    if not listings:
        message = (
            f"no stored listing carried {ticker} between {start} and {end}; ingest the "
            "security's reference first"
        )
        raise UnknownSplitTickerError(message)
    security_ids = {listing.security_id for listing in listings}
    if len(security_ids) > 1:
        message = (
            f"{ticker} belonged to {len(security_ids)} different securities between {start} "
            f"and {end} (a reused ticker); narrow the range to one holder. Nothing is guessed."
        )
        raise ReusedTickerRangeError(message)
    current = _current_tickers(session, next(iter(security_ids)))
    if ticker not in current:
        detail = (
            f"its current ticker is {', '.join(sorted(current))}"
            if current
            else "it has no current listing (it is delisted)"
        )
        message = (
            f"{ticker} is not a current ticker of its security ({detail}). Coverage through a "
            "retired or delisted symbol is not recorded, because it is not verified that the "
            "provider indexes one: an empty answer could mean 'no split' or 'symbol not "
            "indexed'. Use the current ticker."
        )
        raise RetiredTickerCoverageError(message)
    spans = sorted(
        (
            max(listing.valid_from, start),
            end if listing.valid_to is None else min(listing.valid_to - _ONE_DAY, end),
        )
        for listing in listings
    )
    eras: list[_Era] = []
    for first, last in spans:
        if last < first:
            continue
        if eras and first <= eras[-1].end + _ONE_DAY:
            eras[-1] = _Era(eras[-1].start, max(eras[-1].end, last))
        else:
            eras.append(_Era(first, last))
    return next(iter(security_ids)), eras


class StockSplitIngestor:
    """Writes canonical splits for one security, idempotently."""

    __slots__ = ("_now", "_run_id", "_session")

    def __init__(self, session: Session, *, now: datetime, run: IngestionRun | None = None) -> None:
        self._session = session
        self._now = now
        if run is not None and sa.inspect(run).identity is None:
            message = "run must be flushed before the ingestor can stamp it on rows"
            raise ValueError(message)
        self._run_id = run.id if run is not None else None

    def ingest(
        self,
        splits: Sequence[CanonicalStockSplit],
        *,
        security_id: uuid.UUID,
        source_id: uuid.UUID,
    ) -> tuple[int, int]:
        """Insert new splits, skip identical ones; return ``(written, skipped)``.

        Raises:
            StockSplitConflictError: a stored split's facts differ.
        """
        written = skipped = 0
        for split in sorted(splits, key=lambda s: (s.execution_date, s.provider_split_id)):
            if self._insert_if_absent(security_id=security_id, source_id=source_id, split=split):
                written += 1
                continue
            existing = self._session.scalar(
                sa.select(StockSplit).where(
                    StockSplit.source_id == source_id,
                    StockSplit.provider_split_id == split.provider_split_id,
                )
            )
            if existing is None:  # pragma: no cover - unreachable in READ COMMITTED
                message = f"split {split.provider_split_id} reported as held but not visible"
                raise RuntimeError(message)
            differences = conflicting_split_fields(existing, split, security_id)
            if differences:
                raise StockSplitConflictError(
                    provider_split_id=split.provider_split_id, differences=differences
                )
            skipped += 1
        return written, skipped

    def record_coverage(
        self,
        eras: Sequence[_Era],
        *,
        security_id: uuid.UUID,
        source_id: uuid.UUID,
        ticker: str,
    ) -> int:
        """Record each era as covered; return how many were newly recorded."""
        recorded = 0
        for era in eras:
            result = self._session.execute(
                postgresql.insert(StockSplitCoverage)
                .values(
                    id=uuid.uuid4(),
                    security_id=security_id,
                    source_id=source_id,
                    provider_ticker=ticker,
                    covered_from=era.start,
                    covered_to=era.end,
                    ingestion_run_id=self._run_id,
                    created_at=self._now,
                )
                .on_conflict_do_nothing(
                    index_elements=["source_id", "security_id", "covered_from", "covered_to"]
                )
                .returning(StockSplitCoverage.id)
            )
            if result.scalar_one_or_none() is not None:
                recorded += 1
        return recorded

    def _insert_if_absent(
        self, *, security_id: uuid.UUID, source_id: uuid.UUID, split: CanonicalStockSplit
    ) -> bool:
        statement = postgresql.insert(StockSplit).values(
            id=uuid.uuid4(),
            security_id=security_id,
            source_id=source_id,
            provider_split_id=split.provider_split_id,
            provider_ticker=split.ticker,
            execution_date=split.execution_date,
            old_shares=split.old_shares,
            new_shares=split.new_shares,
            adjustment_type=split.adjustment_type.value,
            ingestion_run_id=self._run_id,
            ingested_at=self._now,
            created_at=self._now,
        )
        result = self._session.execute(
            statement.on_conflict_do_nothing(
                index_elements=["source_id", "provider_split_id"]
            ).returning(StockSplit.id)
        )
        return result.scalar_one_or_none() is not None


class SplitIngestionService:
    """Runs split ingestion for one ticker and range against a :class:`SplitProvider`."""

    __slots__ = ("_now", "_provider", "_session", "_store")

    def __init__(
        self,
        *,
        session: Session,
        provider: SplitProvider,
        store: RawResponseStore,
        now: datetime,
    ) -> None:
        self._session = session
        self._provider = provider
        self._store = store
        self._now = now

    async def ingest_stock_splits(
        self, ticker: str, start: date, end: date, *, provider_history_start: date | None
    ) -> RunOutcome:
        """Fetch a ticker's splits and record coverage of ``[start, end]`` if complete.

        ``provider_history_start`` is the earliest date the caller knows the provider plan
        serves (the Massive Basic plan serves only about two years). It is required, with
        ``None`` meaning "the caller attests the plan serves all history", so that no caller
        can claim coverage older than the plan serves by forgetting to say.

        Raises:
            InvalidSplitRangeError: ``end`` precedes ``start``, ``start`` is before the plan's
                history, or ``end`` is within the safety margin of the day of this fetch.
            UnknownSplitTickerError / AmbiguousSplitSecurityError: the ticker does not
                resolve to exactly one stored security over the range.
            RetiredTickerCoverageError: the ticker is not the security's latest.
            IncompleteSplitFetchError: a record was rejected.
            UnmappedSplitError: a split inside the range falls outside every ticker era.
            StockSplitConflictError: a stored split disagrees with the incoming facts.
        """
        symbol = ticker.strip().upper()
        if end < start:
            message = f"end {end} precedes start {start}"
            raise InvalidSplitRangeError(message)
        if provider_history_start is not None and start < provider_history_start:
            message = (
                f"start {start} is before {provider_history_start}, the earliest date the "
                "provider plan is known to serve; coverage claimed that far back could be false"
            )
            raise InvalidSplitRangeError(message)
        today = self._now.astimezone(UTC).date()
        latest_claimable = today - timedelta(days=COVERAGE_SAFETY_MARGIN_DAYS)
        if end > latest_claimable:
            message = (
                f"end {end} is within {COVERAGE_SAFETY_MARGIN_DAYS} days of today "
                f"({today} UTC); the latest claimable date is {latest_claimable}. Coverage "
                "may only claim dates safely in the past, because a provider's answer about "
                "the future -- or about the last few days, before every record is published -- "
                "is not evidence that nothing happened"
            )
            raise InvalidSplitRangeError(message)
        # Resolved before a run opens: a ticker that does not resolve to exactly one
        # security (or is retired) is a usage problem that makes no provider call, so it
        # leaves no failed-run row, like an invalid range.
        security_id, eras = _resolve_eras(self._session, symbol, start, end)

        self._session.info.pop(FAILED_RUN_RECORDED, None)
        with RunRecorder(
            session=self._session,
            store=self._store,
            provider=self._provider.name,
            dataset="stock-splits",
            request_parameters={
                "ticker": symbol,
                "start": start.isoformat(),
                "end": end.isoformat(),
            },
            now=self._now,
        ) as run:
            source = get_or_create_data_source(
                self._session,
                name=self._provider.name,
                provider_type=ProviderType.MARKET_DATA_VENDOR,
            )
            try:
                result = await self._provider.get_stock_splits(symbol)
            except ProviderError as exc:
                store_rejected_captures(run, self._store, exc.captures)
                raise
            if result.rejected:
                run.count_received(len(result.splits) + len(result.rejected))
                run.count_failed(len(result.rejected))
                store_rejected_captures(run, self._store, result.captures)
                message = (
                    f"{len(result.rejected)} split record(s) for {symbol} were rejected, so the "
                    f"fetch cannot be called complete and no coverage is recorded; first: "
                    f"{result.rejected[0]}"
                )
                raise IncompleteSplitFetchError(message)
            if result.captures:
                run.record_captures(self._store.store_all(result.captures))

            # A record outside the requested range is harmless (the provider returns the whole
            # ticker history) and is skipped. A record INSIDE the range must land inside an era:
            # otherwise the ticker -> security mapping is unproven for that date and the fetch
            # is not clean, so the run fails and writes no coverage.
            in_range: list[CanonicalStockSplit] = []
            for split in result.splits:
                if not start <= split.execution_date <= end:
                    continue
                if not any(era.start <= split.execution_date <= era.end for era in eras):
                    message = (
                        f"split {split.provider_split_id} ({split.ticker} on "
                        f"{split.execution_date}) is inside the requested range {start}..{end} "
                        "but outside every period QuantForge can show this ticker belonging to "
                        "one security; no coverage is recorded. Narrow the range to the "
                        "listing history or repair the reference data"
                    )
                    # The fetch itself succeeded; keep that fact on the durable failed row.
                    run.count_received(len(result.splits))
                    run.count_failed(1)
                    raise UnmappedSplitError(message)
                in_range.append(split)

            ingestor = StockSplitIngestor(self._session, now=self._now, run=run.row)
            written, skipped = ingestor.ingest(
                in_range, security_id=security_id, source_id=source.id
            )
            ingestor.record_coverage(
                eras, security_id=security_id, source_id=source.id, ticker=symbol
            )
            run.count_received(len(result.splits))
            run.count_written(written)
            run.count_skipped(skipped + len(result.splits) - len(in_range))
            outcome = run.succeed(self._now)
            logger.info("stock splits for %s %s..%s: %s", symbol, start, end, outcome)
            return outcome
