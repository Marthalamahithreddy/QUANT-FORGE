"""Idempotent benchmark identity resolution and daily-level ingestion.

Mirrors :mod:`quantforge.ingestion.daily`'s contract exactly, applied to
:class:`~quantforge.models.benchmark.BenchmarkPriceBar` instead of
:class:`~quantforge.models.market.DailyPriceBar`:

* **absent** -- insert it.
* **present and identical** -- skip it, counted as ``records_skipped``.
* **present and different** -- refuse, counted as ``records_failed``, never
  overwritten.

``BenchmarkPriceBar`` stores only ``close`` -- no open/high/low/volume,
deliberately, per its own model docstring: an index or ETF reference level is
quoted once per session, and carrying OHLC would invite summing a level as
if it were a traded price. The conflict comparison below is therefore
narrower than ``daily.py``'s (one field, not five), but the policy is
identical, for the identical reason: a vendor silently revising a historical
level must be surfaced, never silently applied.

Canonical type reuse, not a parallel one
------------------------------------------
Bars are mapped through the existing
:func:`quantforge.providers.massive.mapper.to_canonical_daily_bar` into the
existing :class:`~quantforge.ingestion.canonical.CanonicalDailyBar` --
*not* a new ``CanonicalBenchmarkBar``. Nothing in ``CanonicalDailyBar``'s
invariants (OHLC coherence, a positive finite price, no float) is
listing-specific: they are general daily-bar facts about one session's
trading, equally true of a benchmark's daily aggregate as of an equity's.
This module simply reads fewer of its fields (``trading_session_date``,
``close``, ``observed_at``) when writing a ``BenchmarkPriceBar`` row than
:mod:`quantforge.ingestion.daily` does when writing a ``DailyPriceBar`` one.
Introducing a second, near-identical canonical type purely so its name said
"benchmark" would duplicate the validation this one already provides for no
behavioural gain.

Benchmark identity is resolved, never guessed
-----------------------------------------------
:func:`get_or_create_benchmark` is the deterministic get-or-create this
ingestion path needs before it can write a single level: every
``BenchmarkPriceBar`` has a mandatory ``benchmark_id``.
``Benchmark.symbol`` carries no uniqueness constraint in the schema (by
design -- see :mod:`quantforge.models.benchmark`: the same logical index is
published under different vendor symbols, and a symbol can be reused for a
different index years later). That means a bare "match on symbol" is not
safe to treat as identity on its own: this function also requires the
human-readable ``name`` to match whatever is already stored under that
symbol, and refuses -- rather than silently adopting or renaming -- a row
that shares the symbol but means something else.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from quantforge.ingestion.canonical import CanonicalDailyBar, ResolvedVenue
from quantforge.ingestion.daily import IngestCounts
from quantforge.models.benchmark import Benchmark, BenchmarkPriceBar
from quantforge.models.enums import PriceBasis
from quantforge.models.ingestion import IngestionRun

__all__ = [
    "BENCHMARK_VENUE",
    "BenchmarkBarConflictError",
    "BenchmarkBarIngestor",
    "BenchmarkIdentityConflict",
    "conflicting_benchmark_fields",
    "get_or_create_benchmark",
]

logger = logging.getLogger(__name__)

#: Stands in for a stored ``Listing``/``Exchange`` purely so
#: ``MassiveClient.get_daily_bars`` has a venue to convert a daily
#: aggregate's instant into a session date -- a benchmark has no listing and
#: this value is never persisted anywhere: ``BenchmarkPriceBar`` has no
#: exchange/venue column at all, and nothing in :mod:`quantforge.research`
#: ever reads an ingestion-side venue object, so there is no path by which
#: this could leak into a research result. ``America/New_York`` is correct
#: for every mainstream US-listed equity or ETF regardless of its exact
#: primary venue (NYSE, Nasdaq, NYSE Arca, ...): they share one trading-day
#: calendar, and SPY itself primary-lists on NYSE Arca, which observes it.
#: The ``mic``/``name`` values are deliberately not a real ISO 10383 code --
#: ``"BENCH"`` reads as obviously synthetic in a log line or an error
#: message, rather than implying a specific, possibly wrong, real venue such
#: as NYSE Arca. It is also five characters, one more than
#: ``Exchange.mic``'s ``CHECK (mic ~ '^[A-Z0-9]{4}$')`` allows -- so even a
#: future bug that tried to persist this value as a real ``Exchange`` row
#: would be rejected by the database, not merely by convention.
BENCHMARK_VENUE = ResolvedVenue(
    mic="BENCH",
    name="US equity market session (benchmark ingestion)",
    timezone_name="America/New_York",
)

#: Scale of the ``NUMERIC(20, 6)`` ``close`` column. Matches
#: ``quantforge.ingestion.daily``'s own ``_PRICE_SCALE``/``_PRICE_ROUNDING`` --
#: the two must never drift, since they exist to answer the same question
#: ("would this write the same bytes PostgreSQL already stored?") for the
#: same column type.
_PRICE_SCALE = Decimal("0.000001")
_PRICE_ROUNDING = ROUND_HALF_UP


def _quantise(value: Decimal) -> Decimal:
    return value.quantize(_PRICE_SCALE, rounding=_PRICE_ROUNDING)


def conflicting_benchmark_fields(
    stored: BenchmarkPriceBar, incoming: CanonicalDailyBar
) -> dict[str, tuple[Decimal, Decimal]]:
    """Fields where ``incoming`` disagrees with the already-stored level.

    Only ``close`` exists to compare -- see the module docstring on why
    ``BenchmarkPriceBar`` carries nothing else. Quantised at the column's
    scale first, exactly as :func:`quantforge.ingestion.daily.conflicting_fields`
    does, so a re-run of an unchanged series is never reported as a conflict
    against PostgreSQL's own rounding.
    """
    stored_close = _quantise(stored.close)
    incoming_close = _quantise(incoming.close)
    if stored_close != incoming_close:
        return {"close": (stored_close, incoming_close)}
    return {}


class BenchmarkBarConflictError(RuntimeError):
    """A stored benchmark level disagrees with the incoming one for the same key.

    Raised when the database already holds a row for ``(benchmark_id,
    trading_date, source_id, price_basis)`` but its ``close`` differs from what
    the provider is saying now. A level on a different price basis is a
    separate legitimate observation, never a conflict. The existing row is never overwritten; a
    correction arrives as a new run under a new ``ingested_at``, exactly as
    :class:`quantforge.ingestion.daily.ProviderDataConflict` already
    establishes for stock bars.
    """

    def __init__(
        self,
        *,
        symbol: str,
        trading_date: date,
        differences: dict[str, tuple[Decimal, Decimal]],
        price_basis: PriceBasis | None = None,
    ) -> None:
        self.symbol = symbol
        self.price_basis = price_basis
        self.trading_date = trading_date
        self.differences = differences
        detail = "; ".join(
            f"{field}: stored={stored!r} incoming={incoming!r}"
            for field, (stored, incoming) in sorted(differences.items())
        )
        super().__init__(
            f"{symbol} {trading_date.isoformat()}"
            f"{f' [{price_basis.value}]' if price_basis is not None else ''}: "
            "stored benchmark level differs from "
            f"the incoming one ({detail}). The existing row is left untouched. A vendor "
            "revising history is not applied automatically; decide explicitly whether to "
            "keep the stored value or restate the series."
        )


class BenchmarkIdentityConflict(RuntimeError):  # noqa: N818 - "Conflict" names the ingestion-contract outcome, matching ProviderDataConflict's own precedent
    """An existing :class:`Benchmark` row cannot be reused for this request.

    Raised instead of silently creating a second row for the same symbol, or
    silently renaming/repurposing the row that is already there -- either of
    which would quietly change what a stored ``benchmark_id`` has always
    meant to every ``BenchmarkPriceBar`` and :class:`EventStudyResult`
    already attached to it.
    """


def get_or_create_benchmark(
    session: Session,
    *,
    name: str,
    symbol: str,
    currency: str | None = None,
    source_id: uuid.UUID | None = None,
) -> Benchmark:
    """Return the benchmark identified by ``symbol``/``name``, creating it if absent.

    Args:
        session: Caller's session. Nothing is committed.
        name: The full, explicit human-readable identity, e.g.
            ``"SPY (ETF proxy for the S&P 500)"``. Never defaulted or
            inferred -- see :mod:`quantforge.research.event_study` on why
            benchmark selection stays explicit end to end.
        symbol: Vendor/display symbol, e.g. ``"SPY"``. Looked up as-is;
            callers are expected to pass the same canonical form (upper
            case, for a US ticker) every time, matching the convention
            :class:`~quantforge.models.security.Listing` already uses for
            tickers.
        currency: Recorded on creation only, like
            :func:`quantforge.ingestion.sources.get_or_create_data_source`'s
            ``base_url``.
        source_id: Recorded on creation only.

    Returns:
        The existing or newly created row, with its id populated.

    Raises:
        BenchmarkIdentityConflict: If more than one existing row shares
            ``symbol`` (the schema places no uniqueness on it -- see
            :mod:`quantforge.models.benchmark` -- so this is a real
            possibility, not a defensive guard against the impossible), or
            if exactly one does but its stored ``name`` differs from
            ``name``. Either case means "symbol" alone is not a safe enough
            match to proceed on, and the caller must resolve the ambiguity
            by hand rather than have it resolved silently.
    """
    existing = list(session.scalars(sa.select(Benchmark).where(Benchmark.symbol == symbol)))

    if not existing:
        created = Benchmark(name=name, symbol=symbol, currency=currency, source_id=source_id)
        session.add(created)
        session.flush()
        return created

    if len(existing) > 1:
        message = (
            f"{len(existing)} existing Benchmark rows already share symbol {symbol!r} "
            f"({[str(row.id) for row in existing]}); cannot determine which one this "
            "ingestion means without an explicit benchmark_id"
        )
        raise BenchmarkIdentityConflict(message)

    [found] = existing
    if found.name != name:
        message = (
            f"an existing Benchmark row ({found.id}) already uses symbol {symbol!r} under "
            f"the name {found.name!r}, not {name!r}; refusing to silently rename it or "
            "create a second row for the same symbol. Pass the existing name, or resolve "
            "the identity by hand if this is genuinely a different instrument"
        )
        raise BenchmarkIdentityConflict(message)

    return found


class BenchmarkBarIngestor:
    """Writes canonical daily bars as benchmark levels, idempotently.

    The benchmark counterpart of
    :class:`quantforge.ingestion.daily.DailyBarIngestor`. See that class's
    docstring for the full contract this mirrors; the storage target and the
    narrower conflict comparison (``close`` alone) differ, and so does this
    constructor: since migration 0013 ``BenchmarkPriceBar`` has a nullable
    ``ingestion_run_id``, so a run given to the constructor is stamped on every NEW
    level it writes. An identical level that already exists is skipped and keeps the
    run that first wrote it (provenance is never rewritten); a differing level is a
    conflict and is never overwritten. The run passed to :meth:`ingest` records counts.
    """

    __slots__ = ("_now", "_run_id", "_session")

    def __init__(self, session: Session, *, now: datetime, run: IngestionRun | None = None) -> None:
        self._session = session
        self._now = now
        # A pending run's id is None until flushed: stamping it would store a key that belongs to
        # no row, a silently false provenance claim (same guard as DailyBarIngestor).
        if run is not None and sa.inspect(run).identity is None:
            message = "run must be flushed before the ingestor can stamp it on rows"
            raise ValueError(message)
        self._run_id = run.id if run is not None else None

    def ingest(
        self,
        bars: Sequence[CanonicalDailyBar],
        *,
        benchmark_id: uuid.UUID,
        source_id: uuid.UUID,
        symbol: str = "",
        run: IngestionRun | None = None,
    ) -> IngestCounts:
        """Write the levels that are new, skip the ones already identical.

        Raises:
            BenchmarkBarConflictError: If any stored level disagrees.
                Everything before the conflict is left in the session for
                the caller to commit or roll back, exactly as
                :meth:`quantforge.ingestion.daily.DailyBarIngestor.ingest`
                behaves.
        """
        if run is not None and self._run_id is not None and run.id != self._run_id:
            message = "the run recording counts must be the run stamped on the written levels"
            raise ValueError(message)
        counts = IngestCounts(received=len(bars))
        written = skipped = failed = 0

        for bar in sorted(bars, key=lambda b: b.trading_session_date):
            inserted = self._insert_if_absent(
                benchmark_id=benchmark_id, source_id=source_id, bar=bar
            )
            if inserted:
                written += 1
                continue

            existing = self._existing(benchmark_id=benchmark_id, bar=bar, source_id=source_id)
            if existing is None:  # pragma: no cover - unreachable in READ COMMITTED
                message = (
                    f"ON CONFLICT DO NOTHING reported the key "
                    f"({benchmark_id}, {bar.trading_session_date}, {source_id}, "
                    f"{bar.price_basis.value}) as held, "
                    "but no committed row was visible on re-read"
                )
                raise RuntimeError(message)
            differences = conflicting_benchmark_fields(existing, bar)
            if differences:
                failed += 1
                raise BenchmarkBarConflictError(
                    symbol=symbol or str(benchmark_id),
                    trading_date=bar.trading_session_date,
                    differences=differences,
                    price_basis=bar.price_basis,
                )
            skipped += 1

        totals = IngestCounts(
            received=counts.received, written=written, skipped=skipped, failed=failed
        )
        if run is not None:
            run.records_received = totals.received
            run.records_written = totals.written
            run.records_skipped = totals.skipped
            run.records_failed = totals.failed
        logger.info(
            "ingested %s benchmark levels for %s: %d written, %d skipped, %d failed",
            symbol or benchmark_id,
            benchmark_id,
            written,
            skipped,
            failed,
        )
        return totals

    def _existing(
        self, *, benchmark_id: uuid.UUID, bar: CanonicalDailyBar, source_id: uuid.UUID
    ) -> BenchmarkPriceBar | None:
        return self._session.scalar(
            sa.select(BenchmarkPriceBar).where(
                BenchmarkPriceBar.benchmark_id == benchmark_id,
                BenchmarkPriceBar.trading_date == bar.trading_session_date,
                BenchmarkPriceBar.source_id == source_id,
                BenchmarkPriceBar.price_basis == bar.price_basis,
            )
        )

    def _insert_if_absent(
        self, *, benchmark_id: uuid.UUID, source_id: uuid.UUID, bar: CanonicalDailyBar
    ) -> bool:
        """Insert the level if ``(benchmark_id, trading_date, source_id, price_basis)`` is free.

        Same atomic ``ON CONFLICT DO NOTHING`` pattern as
        :meth:`quantforge.ingestion.daily.DailyBarIngestor._insert_if_absent`,
        for the identical concurrency reason: two ingestors racing over the
        same range must not turn into an unhandled ``IntegrityError``.
        """
        statement = postgresql.insert(BenchmarkPriceBar).values(
            id=uuid.uuid4(),
            benchmark_id=benchmark_id,
            trading_date=bar.trading_session_date,
            close=bar.close,
            price_basis=bar.price_basis,
            source_id=source_id,
            observed_at=bar.observed_at,
            ingestion_run_id=self._run_id,
            ingested_at=self._now,
            created_at=self._now,
        )
        insert_stmt = statement.on_conflict_do_nothing(
            index_elements=["benchmark_id", "trading_date", "source_id", "price_basis"]
        ).returning(BenchmarkPriceBar.id)
        result = self._session.execute(insert_stmt)
        return result.scalar_one_or_none() is not None
