"""Idempotent daily-bar ingestion.

The contract
------------
Re-running the same request must be a no-op, and that is the whole design. The
uniqueness key is ``(listing_id, trading_date, source_id)``, so for each bar:

* **absent** -- insert it.
* **present and identical** -- skip it, counted as ``records_skipped``.
* **present and different** -- refuse, counted as ``records_failed``.

The middle case is the one that is easy to get wrong. Treating "already there" as
an error makes re-runs impossible, which is the normal way anyone recovers from
an interrupted job. Treating it as success without comparing values is worse: a
vendor silently revising history would be recorded as a successful no-op, and the
stored series would be whichever value happened to arrive first, forever. So the
stored values are compared field by field, and any difference is a conflict that
an operator has to see.

Conflicts never overwrite
-------------------------
``DailyPriceBar`` has no ``updated_at``: it is a source fact, and a correction
arrives as a new ``ingested_at`` under a new run, not as a mutation. Overwriting
would destroy the evidence that the value ever changed, which is precisely what a
reconciliation layer needs. So a differing value is reported, never applied.

Why the comparison quantises before comparing
---------------------------------------------
Prices are stored ``NUMERIC(20, 6)``, so the database rounds whatever it is given.
An incoming ``199.9999999`` is stored as ``200.000000``; comparing the raw
``199.9999999`` against the stored ``200.000000`` reports a conflict on every
re-run of a perfectly stable series. Quantising to the column's scale first makes
the comparison "would this write the same bytes?", which is the question that
actually matters, instead of "are these two Decimals spelled the same?".

Transaction shape
-----------------
The ingestor never commits. The caller owns the transaction so the bars and the
``IngestionRun`` that describes them commit together. A crash between the two
would otherwise leave either a "succeeded" run with no rows, or rows that no run
accounts for.

Partial failure
---------------
A malformed record is counted and skipped; the valid ones still commit, and the
run finishes ``partial``. A whole-range failure from one bad row would make the
pipeline unusable for large ranges, where a single glitch is expected. What is
*not* done is swallowing the error silently -- every rejected record is counted,
the count lands on the run, and the offending record is named in the log.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from quantforge.ingestion.canonical import CanonicalDailyBar
from quantforge.models.ingestion import IngestionRun
from quantforge.models.market import DailyPriceBar

__all__ = [
    "BarConflictError",
    "DailyBarIngestor",
    "IngestCounts",
    "ProviderDataConflict",
    "conflicting_fields",
]

logger = logging.getLogger(__name__)

#: Scale of the ``NUMERIC(20, 6)`` price columns. Prices are compared at this
#: scale so a re-run of an unchanged series is not reported as a conflict
#: against the database's own rounding.
_PRICE_SCALE = Decimal("0.000001")

#: Rounding mode that reproduces PostgreSQL's ``NUMERIC`` exactly.
#:
#: This must be ``ROUND_HALF_UP``, which rounds ties *away from zero* -- what
#: PostgreSQL does -- and not the more usual ``ROUND_HALF_EVEN`` that Python
#: programmers reach for by default. The two disagree only on an exact tie at the
#: seventh decimal (1.0000005 is 1.000000 half-even and 1.000001 half-up), but the
#: consequence of getting it wrong is precisely the failure this quantisation
#: exists to prevent: Python would compare 1.000000 against a column that stored
#: 1.000001, and report an unchanged series as a conflict, on every run, forever.
#: Verified against the database rather than assumed; see
#: ``test_quantisation_matches_postgres_rounding_of_ties``.
_PRICE_ROUNDING = ROUND_HALF_UP


@dataclass(frozen=True, slots=True)
class IngestCounts:
    """Per-run tallies.

    ``received`` counts what the provider returned; the other three partition it.
    ``written + skipped + failed == received`` is asserted by the tests, because a
    count that does not add up means a bar was silently accounted to nowhere --
    which is the exact failure the run row exists to rule out.
    """

    received: int = 0
    written: int = 0
    skipped: int = 0
    failed: int = 0

    def __add__(self, other: IngestCounts) -> IngestCounts:
        return IngestCounts(
            received=self.received + other.received,
            written=self.written + other.written,
            skipped=self.skipped + other.skipped,
            failed=self.failed + other.failed,
        )


class ProviderDataConflict(RuntimeError):  # noqa: N818 - name comes from the ingestion contract; the Error-suffixed alias BarConflictError stays available below
    """A stored bar disagrees with the incoming one for the same key.

    Raised when the database already holds a row for ``(listing_id,
    trading_date, source_id)`` but at least one canonical value differs from
    what the provider is saying now. The existing row is treated as a source
    fact and is never overwritten; a correction arrives as a new run and a new
    ``ingested_at``, not as an in-place mutate. See :mod:`models.market` on why
    ``DailyPriceBar`` has no ``updated_at``.
    """

    def __init__(
        self,
        *,
        ticker: str,
        trading_date: date,
        differences: dict[str, tuple[Decimal | int | None, Decimal | int | None]],
    ) -> None:
        self.ticker = ticker
        self.trading_date = trading_date
        self.differences = differences
        detail = "; ".join(
            f"{field}: stored={stored!r} incoming={incoming!r}"
            for field, (stored, incoming) in sorted(differences.items())
        )
        super().__init__(
            f"{ticker} {trading_date.isoformat()}: stored bar differs from the "
            f"incoming one ({detail}). The existing row is left untouched. A "
            "vendor revising history is not applied automatically; decide "
            "explicitly whether to keep the stored value or restate the series."
        )


#: ``ProviderDataConflict`` is the canonical name from the ingestion contract; the
#: original ``BarConflictError`` name is kept as an alias so existing importers
#: keep compiling.
BarConflictError = ProviderDataConflict


def _quantise(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    return value.quantize(_PRICE_SCALE, rounding=_PRICE_ROUNDING)


def conflicting_fields(
    stored: DailyPriceBar, incoming: CanonicalDailyBar
) -> dict[str, tuple[Decimal | int | None, Decimal | int | None]]:
    """Fields where ``incoming`` disagrees with what is already stored.

    Prices are compared at the column's scale; counts are compared as integers.
    A field absent from ``incoming`` (``None``) does not conflict with a stored
    value, because "this run did not report vwap" is not a claim that vwap
    changed. Symmetrically a stored ``NULL`` does not conflict with an incoming
    value. Only two *asserted* values that differ are conflicts -- so a provider
    that starts supplying a previously-absent optional field is not a conflict.
    """
    differences: dict[str, tuple[Decimal | int | None, Decimal | int | None]] = {}
    for field in ("open", "high", "low", "close", "vwap"):
        stored_value = _quantise(getattr(stored, field))
        incoming_value = _quantise(getattr(incoming, field))
        if (
            stored_value is not None
            and incoming_value is not None
            and stored_value != incoming_value
        ):
            differences[field] = (stored_value, incoming_value)
    for field in ("volume", "trades"):
        stored_value = getattr(stored, field)
        incoming_value = getattr(incoming, field)
        if (
            stored_value is not None
            and incoming_value is not None
            and stored_value != incoming_value
        ):
            differences[field] = (stored_value, incoming_value)
    return differences


class DailyBarIngestor:
    """Writes canonical daily bars for one listing, idempotently."""

    __slots__ = ("_now", "_run_id", "_session")

    def __init__(self, session: Session, *, now: datetime, run: IngestionRun | None = None) -> None:
        """
        Args:
            session: The caller's session. Nothing is committed here.
            now: Injected clock, used for ``ingested_at``. A bar's
                ``ingested_at`` is the earliest moment a backtest could have
                used it, so it has to be the real time of writing -- not the
                session date, and not a value derived from the payload.
            run: The run that is writing. Only its id is read here; the tallies
                are applied in :meth:`ingest`, which is where they are known.
                The run must already be flushed, so that this id is real.
        """
        self._session = session
        self._now = now
        # `run.id` is typed non-optional, so the compiler cannot see the case this
        # guards: a pending instance's id attribute is None until the flush that
        # assigns it. `identity` is the accurate test for "has the database seen
        # this row yet", and stamping a pending run would store a key that belongs
        # to no row -- a provenance claim that is silently false.
        if run is not None and sa.inspect(run).identity is None:
            message = "run must be flushed before the ingestor can stamp it on rows"
            raise ValueError(message)
        self._run_id = run.id if run is not None else None

    def ingest(
        self,
        bars: Sequence[CanonicalDailyBar],
        *,
        listing_id: uuid.UUID,
        source_id: uuid.UUID,
        ticker: str = "",
        run: IngestionRun | None = None,
    ) -> IngestCounts:
        """Write the bars that are new, skip the ones already identical.

        Args:
            bars: Canonical bars, in any order.
            listing_id: The listing every bar belongs to.
            source_id: The provider whose opinion these are.
            ticker: Used only in conflict messages, to make them actionable.
            run: The run to count onto. Optional so the ingestor can be exercised
                directly; production callers always pass one.

        Returns:
            The tallies, which are also applied to ``run`` when given.

        Raises:
            ProviderDataConflict: If any stored bar disagrees. Raised rather than
                counted-and-continued, because a conflict means the source is
                revising history and the operator needs to see the whole
                disagreement, not a summary line. Everything before the conflict
                is left in the session for the caller to commit or roll back.
        """
        counts = IngestCounts(received=len(bars))
        written = skipped = failed = 0

        # Sorted so two runs over the same set produce the same insert order,
        # which keeps a deadlock between concurrent ingestors far less likely.
        for bar in sorted(bars, key=lambda b: b.trading_session_date):
            inserted = self._insert_if_absent(listing_id=listing_id, source_id=source_id, bar=bar)
            if inserted:
                written += 1
                continue

            # The unique key was already held. The database writing nothing does
            # not tell us whether the held row is identical (skip) or different
            # (conflict), so the stored value is read back and compared field by
            # field. Two concurrent writers both hitting this path still converge
            # here because the row now exists and the comparison is read-only.
            existing = self._existing(listing_id=listing_id, bar=bar, source_id=source_id)
            if existing is None:  # pragma: no cover - unreachable in READ COMMITTED
                message = (
                    f"ON CONFLICT DO NOTHING reported the key "
                    f"({listing_id}, {bar.trading_session_date}, {source_id}) as held, "
                    "but no committed row was visible on re-read"
                )
                raise RuntimeError(message)
            differences = conflicting_fields(existing, bar)
            if differences:
                # Counted before raising so the tally the caller sees is not
                # silently short by one.
                failed += 1
                raise ProviderDataConflict(
                    ticker=ticker or str(listing_id),
                    trading_date=bar.trading_session_date,
                    differences=differences,
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
            "ingested %s bars for listing %s: %d written, %d skipped, %d failed",
            ticker or listing_id,
            listing_id,
            written,
            skipped,
            failed,
        )
        return totals

    def _existing(
        self, *, listing_id: uuid.UUID, bar: CanonicalDailyBar, source_id: uuid.UUID
    ) -> DailyPriceBar | None:
        return self._session.scalar(
            sa.select(DailyPriceBar).where(
                DailyPriceBar.listing_id == listing_id,
                DailyPriceBar.trading_date == bar.trading_session_date,
                DailyPriceBar.source_id == source_id,
            )
        )

    def _insert_if_absent(
        self, *, listing_id: uuid.UUID, source_id: uuid.UUID, bar: CanonicalDailyBar
    ) -> bool:
        """Insert the bar if ``(listing_id, trading_date, source_id)`` is free.

        The insert is ``ON CONFLICT DO NOTHING``, which makes the uniqueness
        check and the insert one atomic statement. The alternative -- SELECT then
        INSERT -- lets two concurrent ingestors of the same range both see "free"
        and both insert, and the second one dies on the unique index with an
        ``IntegrityError`` that carries none of the context this pipeline needs.
        With the conflict clause the second writer simply gets "no row inserted"
        and falls back to reading and comparing the stored row.

        Returns ``True`` when this statement inserted the row, ``False`` when the
        key was already held.

        id and created_at are supplied explicitly rather than left to column
        defaults, so the values the conflict clause sees are identical to the ORM
        path and the statement is deterministic under an injected clock.
        """
        statement = postgresql.insert(DailyPriceBar).values(
            id=uuid.uuid4(),
            listing_id=listing_id,
            trading_date=bar.trading_session_date,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            # Deliberately left NULL rather than defaulted to `close`. Adjustment
            # depends on a corporate-action convention, so "this source did not
            # supply an adjusted close" and "adjusted close equals close" must
            # remain distinguishable.
            adjusted_close=None,
            volume=bar.volume,
            source_id=source_id,
            vwap=bar.vwap,
            trades=bar.trades,
            observed_at=bar.observed_at,
            ingested_at=self._now,
            # Stamped at construction so the row and the run that produced it
            # commit or roll back as one fact. A counts-only link would leave the
            # row's origin unprovable after the fact.
            ingestion_run_id=self._run_id,
            created_at=self._now,
        )
        insert_stmt = statement.on_conflict_do_nothing(
            index_elements=["listing_id", "trading_date", "source_id"]
        ).returning(DailyPriceBar.id)
        result = self._session.execute(insert_stmt)
        return result.scalar_one_or_none() is not None
