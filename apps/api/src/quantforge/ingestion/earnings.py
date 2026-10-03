"""Idempotent earnings-event ingestion.

The contract
------------
Re-running the same request must be a no-op, and *not* overwriting a revision
must be a no-op too. Both fall out of one uniqueness key:
``(source_id, company_id, fingerprint)``, where ``fingerprint`` is a
deterministic SHA-256 over the canonical factual fields. For each event:

* **absent** -- insert it.
* **present** (same source, same company, identical facts) -- skip it, counted
  as ``records_skipped``.
* **present under a different fingerprint** -- a different row, inserted
  alongside: a changed estimate or restated figure is a revision, preserved
  next to the original, never a replacement.
* **from a different source** -- never collapsed; ``source_id`` is part of the
  key, so two vendors' views of the same period are two rows by construction.

Why the digest, not the columns
-------------------------------
The four figures, ``fiscal_quarter`` and ``currency`` are nullable, and
PostgreSQL's unique semantics treat ``NULL`` as distinct from ``NULL`` -- so a
unique index over the natural columns would store two identical observations
that happen to lack an optional figure, which is precisely the duplicate a
re-fetch is supposed to skip. A single NOT NULL digest collides when, and only
when, the whole fact set is equal. The digest is per-fact: any change to any
stored field changes it, which is what makes "revision" computable at write
time (insert) rather than a read-time reconciliation.

Conflicts never overwrite
-------------------------
There is no conflict path here at all, and that is the point. A "conflict" is
an observation whose facts differ from an already-stored one, and under this
policy it is not an error: it is a new revision that the schema was designed to
keep. The daily-bar writer refuses differing values because a *session's* facts
are single-valued; an earnings fact set is not, so the two policies are
siblings, not the same rule applied twice.

Transaction shape
-----------------
The ingestor never commits. The caller owns the transaction so the events and
the ``IngestionRun`` that describes them commit together. Every written row is
stamped with the run's id for exactly the same reason ``DailyPriceBar`` is: a
row whose origin cannot be traced to a run is a provenance claim that is
silently unverifiable.

Date-aware resolution
---------------------
A canonical event names a ticker, which resolves to a *listing* over a
validity interval, whose *security* belongs to a *company*. Tickers are
reused by different issuers over time, so "which company" is a question the
event's own date must answer: the listing whose half-open interval
``[valid_from, valid_to)`` contains the event date is the issuer of record for
that event. The current open listing is preferred whenever it covers the date;
a date no stored listing covers is an explicit failure, never a guessed
company.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from quantforge.ingestion.canonical import CanonicalEarningsEvent
from quantforge.ingestion.daily import IngestCounts
from quantforge.models.earnings import EarningsEvent, earnings_event_fingerprint
from quantforge.models.ingestion import IngestionRun
from quantforge.models.security import Listing

__all__ = ["EarningsIngestor", "UnknownIssuerError", "earnings_fingerprint"]

logger = logging.getLogger(__name__)


class UnknownIssuerError(LookupError):
    """The ticker cannot be resolved to a single issuer for the event's date.

    Raised when a canonical event's ticker is not stored under any listing whose
    validity interval covers the event date, or is stored under listings from
    two different issuers at once. The event is counted as failed and the run
    finishes ``partial``; no company is ever fabricated or guessed.
    """


def earnings_fingerprint(event: CanonicalEarningsEvent) -> str:
    """Deterministic SHA-256 over the factual fields of one observation.

    Two observations are the same fact exactly when this digest matches. The
    serialisation is canonical rather than raw, so equal values that are
    *spelled* differently still agree (``1.50`` vs ``1.5``; ``14:00+02:00``
    vs ``12:00Z``), and it covers the event's facts and nothing the writer
    adds: the ticker is deliberately absent (the unique index carries
    ``company_id``) and ``ingested_at`` is deliberately absent (the same fact
    re-fetched later must still collide).

    This is the ingestion-side alias of the single definition owned by the
    model -- :func:`quantforge.models.earnings.earnings_event_fingerprint` --
    so the digest stamped here is provably the one the persistence layer
    recomputes on every write and enforces with the
    ``uq_earnings_event_source_company_fingerprint`` index. It is the
    cryptographic form (raw SHA-256 is a provable commitment) rather than
    Python's per-process-randomised built-in ``hash()``.
    """
    return earnings_event_fingerprint(
        fiscal_year=event.fiscal_year,
        fiscal_quarter=event.fiscal_quarter,
        event_date=event.event_date,
        event_timestamp=event.event_timestamp,
        timing=event.timing,
        reported_eps=event.reported_eps,
        estimated_eps=event.estimated_eps,
        reported_revenue=event.reported_revenue,
        estimated_revenue=event.estimated_revenue,
        currency=event.currency,
        observed_at=event.observed_at,
        observed_at_basis=event.observed_at_basis,
    )


def resolve_company_id(
    session: Session,
    *,
    ticker: str,
    event_date: date,
) -> uuid.UUID:
    """Return the issuer that ``ticker`` belonged to on ``event_date``.

    Raises:
        UnknownIssuerError: If the ticker is not stored, no stored listing
            covers ``event_date``, or two different issuers' listings cover it.
    """
    symbol = ticker.upper()
    listings = list(session.scalars(sa.select(Listing).where(Listing.ticker == symbol)).all())
    if not listings:
        hint = f" Ingest its reference first: quantforge ingestion reference {symbol}."
        raise UnknownIssuerError(f"{symbol} is not stored.{hint}")

    covering = [
        listing
        for listing in listings
        if listing.valid_from <= event_date
        and (listing.valid_to is None or event_date < listing.valid_to)
    ]
    if not covering:
        raise UnknownIssuerError(
            f"{symbol} had no listing covering {event_date.isoformat()} (the "
            "ticker may have been reused or delisted); not attributing the "
            "event to a guessed issuer"
        )

    companies = {listing.security.company_id for listing in covering}
    if len(companies) > 1:
        raise UnknownIssuerError(
            f"{symbol} is covered by listings from {len(companies)} issuers on "
            f"{event_date.isoformat()}; refusing to pick one"
        )
    return companies.pop()


class EarningsIngestor:
    """Writes canonical earnings events, preserving revisions idempotently."""

    __slots__ = ("_now", "_run", "_session")

    def __init__(self, session: Session, *, now: datetime, run: IngestionRun | None = None) -> None:
        """
        Args:
            session: The caller's session. Nothing is committed here.
            now: Injected clock, used for ``ingested_at``. An event's
                ``ingested_at`` is when QuantForge actually stored it -- the
                same fact re-fetched later must get a fresh stamp, never the
                first fetch's.
            run: The run that is writing. Its id is stamped on every written
                row, and its counters are applied in :meth:`ingest`. The run
                must already be flushed, so that this id is real.
        """
        self._session = session
        self._now = now
        if run is not None and sa.inspect(run).identity is None:
            message = "run must be flushed before the ingestor can stamp it on rows"
            raise ValueError(message)
        self._run = run

    def ingest(
        self,
        events: Sequence[CanonicalEarningsEvent],
        *,
        source_id: uuid.UUID,
    ) -> IngestCounts:
        """Write the events that are new, skip the ones already identical.

        Args:
            events: Canonical events, in any order.
            source_id: The provider whose opinion these are.

        Returns:
            The tallies, also applied to ``run`` when one was given. A
            resolution failure is counted as failed -- never a silent drop --
            and the valid events still write, which makes the run ``partial``.
        """
        counts = IngestCounts(received=len(events))
        written = skipped = failed = 0

        # event_timestamp is frequently absent (a date-only observation), so
        # the sort and the issuer lookup both use event_date -- the one
        # temporal fact guaranteed to be present on every event.
        for event in sorted(events, key=lambda item: (item.ticker.upper(), item.event_date)):
            try:
                company_id = resolve_company_id(
                    self._session,
                    ticker=event.ticker,
                    event_date=event.event_date,
                )
            except UnknownIssuerError as exc:
                failed += 1
                logger.warning(
                    "earnings event %s skipped as failed: %s",
                    event.event_date.isoformat(),
                    exc,
                )
                continue
            inserted = self._insert_if_absent(
                company_id=company_id,
                source_id=source_id,
                event=event,
            )
            if inserted:
                written += 1
            else:
                skipped += 1

        totals = IngestCounts(
            received=counts.received, written=written, skipped=skipped, failed=failed
        )
        if totals.received != totals.written + totals.skipped + totals.failed:  # pragma: no cover
            # Unreachable by construction; guards the accounting invariant a run
            # row exists to state plainly.
            message = (
                f"earnings tallies do not add up: received={totals.received} "
                f"written={totals.written} skipped={totals.skipped} "
                f"failed={totals.failed}"
            )
            raise RuntimeError(message)
        if self._run is not None:
            self._run.records_received = totals.received
            self._run.records_written = totals.written
            self._run.records_skipped = totals.skipped
            self._run.records_failed = totals.failed
        logger.info(
            "ingested %d earnings events: %d written, %d skipped, %d failed",
            totals.received,
            written,
            skipped,
            failed,
        )
        return totals

    def _insert_if_absent(
        self,
        *,
        company_id: uuid.UUID,
        source_id: uuid.UUID,
        event: CanonicalEarningsEvent,
    ) -> bool:
        """Insert the event if ``(source_id, company_id, fingerprint)`` is free.

        ``ON CONFLICT DO NOTHING`` makes the uniqueness check and the insert one
        atomic statement, so two concurrent ingestors of the same observation
        cannot both insert; the second simply gets "no row inserted" and is
        counted as a skip. The alternative -- SELECT then INSERT -- loses that
        race, and the loser dies on the unique index with an ``IntegrityError``
        carrying none of the context this pipeline needs.

        The fingerprint is computed here, at the persistence boundary, so the
        digest stored in the row is definitionally the one the index enforces.

        Returns ``True`` when this statement inserted the row, ``False`` when
        the key was already held.

        ``id`` and ``created_at`` are supplied explicitly, exactly as the
        daily-bar writer does, so the statement stays deterministic under an
        injected clock.
        """
        statement = postgresql.insert(EarningsEvent).values(
            id=uuid.uuid4(),
            company_id=company_id,
            fiscal_year=event.fiscal_year,
            fiscal_quarter=event.fiscal_quarter,
            event_date=event.event_date,
            event_timestamp=event.event_timestamp,
            timing=event.timing.value,
            reported_eps=event.reported_eps,
            estimated_eps=event.estimated_eps,
            reported_revenue=event.reported_revenue,
            estimated_revenue=event.estimated_revenue,
            currency=event.currency,
            source_id=source_id,
            fingerprint=earnings_fingerprint(event),
            observed_at=event.observed_at,
            observed_at_basis=(
                event.observed_at_basis.value if event.observed_at_basis is not None else None
            ),
            ingested_at=self._now,
            # Stamped at construction so the row and the run that produced it
            # commit or roll back as one fact.
            ingestion_run_id=self._run.id if self._run is not None else None,
            created_at=self._now,
        )
        insert_stmt = statement.on_conflict_do_nothing(
            index_elements=["source_id", "company_id", "fingerprint"]
        ).returning(EarningsEvent.id)
        result = self._session.execute(insert_stmt)
        return result.scalar_one_or_none() is not None
