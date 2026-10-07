"""Establish ONE filing's acceptance instant from its official EDGAR header, on demand.

Architecture (step 11.2H, docs/sec-acceptance-time-design.md): bulk submissions JSON gives filing
*identity*; ``SecFiling.accepted_at`` is NULL until this service sets it from the filing header, the
only approved source. The submissions JSON ``acceptanceDateTime`` is never a fallback.

* **One accession, one request.** There is no company-wide, "missing", recent or "all" mode and no
  automatic enrichment after bulk ingestion. If the instant is already established, the default
  makes no request at all and returns the stored value.
* **Identity first.** The fetched header must state the requested accession, the stored form, the
  stored filing date and the stored registrant's CIK. A 200 that does not match is refused, and its
  bytes are kept as rejected evidence.
* **The only mutation is NULL -> header instant**, done as a guarded ``UPDATE ... WHERE accepted_at
  IS NULL``. A stored value is never overwritten. In explicit ``verify`` mode a fetched header that
  disagrees with the stored value is a hard :class:`SecAcceptanceConflictError` (the response is kept
  as rejected evidence; nothing changes).
* **Provenance.** The raw header is stored through the normal raw store and the run row links to its
  manifest; the run's request parameters name the accession and registrant.
* **Failure leaves NULL.** HTTP failure, malformed header, identity mismatch, an ambiguous or
  non-existent Eastern time, or a missing field: ``accepted_at`` stays NULL.

The instant is filing-acceptance provenance only: never an earnings ``observed_at``, ``known_as_of``,
a session or tradability evidence, and ``SEC_FILING_ACCEPTANCE`` stays unapproved for point-in-time use.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.orm import Session

from quantforge.ingestion.raw_store import RawResponseStore
from quantforge.ingestion.runs import FAILED_RUN_RECORDED, RunOutcome, RunRecorder
from quantforge.ingestion.service import store_rejected_captures
from quantforge.models.company import Company
from quantforge.models.filing import ACCESSION_REGEX, SecFiling
from quantforge.providers.errors import ProviderError
from quantforge.providers.filings import FilingHeaderProvider, FilingHeaderResult
from quantforge.providers.sec.identifiers import normalise_cik

__all__ = [
    "AcceptanceHeaderMismatchError",
    "AcceptanceReport",
    "AcceptanceStatus",
    "FilingAcceptanceService",
    "FilingNotStoredError",
    "InvalidAcceptanceRequestError",
    "SecAcceptanceConflictError",
]

logger = logging.getLogger(__name__)

_DATASET = "sec-filing-acceptance"


class InvalidAcceptanceRequestError(ValueError):
    """The request is not one dashed accession (and optionally one CIK)."""


class FilingNotStoredError(LookupError):
    """No stored filing has this accession; identity is ingested first, never created here."""


class AcceptanceHeaderMismatchError(ValueError):
    """The fetched header does not describe the stored filing; ``accepted_at`` is left unchanged."""


class SecAcceptanceConflictError(RuntimeError):
    """A header disagrees with an acceptance instant that is already established."""

    def __init__(self, *, accession_number: str, stored: datetime, header: datetime) -> None:
        self.accession_number = accession_number
        self.stored = stored
        self.header = header
        super().__init__(
            f"{accession_number}: stored accepted_at {stored.isoformat()} differs from the EDGAR "
            f"header {header.isoformat()}. Nothing was overwritten; the response is kept as rejected "
            "evidence for an operator to decide."
        )


class AcceptanceStatus(StrEnum):
    """What one enrichment call did."""

    #: NULL -> header instant; a request was made and the raw header stored.
    ESTABLISHED = "ESTABLISHED"
    #: Already established; no request was made.
    ALREADY_ESTABLISHED = "ALREADY_ESTABLISHED"
    #: ``verify`` mode: a request was made and the header equals the stored instant.
    VERIFIED = "VERIFIED"


@dataclass(frozen=True, slots=True)
class AcceptanceReport:
    """Result of one accession-scoped call."""

    accession_number: str
    status: AcceptanceStatus
    accepted_at: datetime
    #: ``None`` when no run was needed (``ALREADY_ESTABLISHED``).
    outcome: RunOutcome | None
    #: ``EDGAR_HEADER`` when this call fetched the header; ``STORED_VALUE`` when no request was
    #: made (the stored value's origin is not re-checked; use ``verify`` to compare it).
    source: str = "EDGAR_HEADER"


class FilingAcceptanceService:
    """Sets ``SecFiling.accepted_at`` from the official header for exactly one accession."""

    __slots__ = ("_now", "_provider", "_session", "_store")

    def __init__(
        self,
        *,
        session: Session,
        provider: FilingHeaderProvider,
        store: RawResponseStore,
        now: datetime,
    ) -> None:
        self._session = session
        self._provider = provider
        self._store = store
        self._now = now

    async def enrich(
        self, accession_number: str, *, cik: str | None = None, verify: bool = False
    ) -> AcceptanceReport:
        """Establish (or, with ``verify``, re-check) one stored filing's acceptance instant."""
        if not ACCESSION_REGEX.fullmatch(accession_number):
            message = f"{accession_number!r} is not a dashed accession (0000320193-24-000123)"
            raise InvalidAcceptanceRequestError(message)
        try:
            wanted_cik = normalise_cik(cik) if cik is not None else None
        except ValueError as exc:
            raise InvalidAcceptanceRequestError(str(exc)) from exc
        filing, registrant_cik = self._stored(accession_number, wanted_cik)

        if filing.accepted_at is not None and not verify:
            return AcceptanceReport(
                accession_number=accession_number,
                status=AcceptanceStatus.ALREADY_ESTABLISHED,
                accepted_at=filing.accepted_at,
                outcome=None,
                source="STORED_VALUE",
            )

        self._session.info.pop(FAILED_RUN_RECORDED, None)
        with RunRecorder(
            session=self._session,
            store=self._store,
            provider=self._provider.name,
            dataset=_DATASET,
            request_parameters={
                "accession": accession_number,
                "cik": registrant_cik,
                "mode": "verify" if verify else "establish",
            },
            now=self._now,
        ) as run:
            try:
                result = await self._provider.get_filing_header(registrant_cik, accession_number)
            except ProviderError as exc:
                store_rejected_captures(run, self._store, exc.captures)
                raise
            header = result.header
            self._require_matching_identity(filing, registrant_cik, result, run)

            if filing.accepted_at is not None:
                stored = filing.accepted_at
                if stored != header.accepted_at:
                    store_rejected_captures(run, self._store, [result.capture])
                    raise SecAcceptanceConflictError(
                        accession_number=accession_number, stored=stored, header=header.accepted_at
                    )
                run.record_captures(self._store.store_all([result.capture]))
                run.count_received(1)
                run.count_skipped(1)
                outcome = run.succeed(self._now)
                return AcceptanceReport(
                    accession_number, AcceptanceStatus.VERIFIED, stored, outcome
                )

            run.record_captures(self._store.store_all([result.capture]))
            updated = self._session.scalars(
                sa.update(SecFiling)
                .where(SecFiling.id == filing.id, SecFiling.accepted_at.is_(None))
                .values(accepted_at=header.accepted_at)
                .returning(SecFiling.id)
            ).all()
            if len(updated) != 1:
                # Another writer established it between our read and the guarded UPDATE.
                current = self._session.scalar(
                    sa.select(SecFiling.accepted_at).where(SecFiling.id == filing.id)
                )
                if current is None:  # pragma: no cover - the row vanished mid-call
                    message = f"{accession_number}: the stored filing disappeared during the call"
                    raise RuntimeError(message)
                if current != header.accepted_at:
                    raise SecAcceptanceConflictError(
                        accession_number=accession_number, stored=current, header=header.accepted_at
                    )
                run.count_received(1)
                run.count_skipped(1)
                return AcceptanceReport(
                    accession_number,
                    AcceptanceStatus.VERIFIED,
                    header.accepted_at,
                    run.succeed(self._now),
                )
            run.count_received(1)
            run.count_written(1)
            outcome = run.succeed(self._now)
            logger.info("sec acceptance for %s established from the EDGAR header", accession_number)
            return AcceptanceReport(
                accession_number, AcceptanceStatus.ESTABLISHED, header.accepted_at, outcome
            )

    def _stored(self, accession_number: str, cik: str | None) -> tuple[SecFiling, str]:
        query = (
            sa.select(SecFiling, Company.cik)
            .join(Company, Company.id == SecFiling.company_id)
            .where(SecFiling.accession_number == accession_number)
        )
        if cik is not None:
            query = query.where(Company.cik == cik)
        rows = self._session.execute(query).all()
        if not rows:
            message = (
                f"no stored filing has accession {accession_number}"
                + (f" for CIK {cik}" if cik else "")
                + "; ingest its identity first (sec-filings-history)"
            )
            raise FilingNotStoredError(message)
        if len(rows) > 1:
            message = (
                f"accession {accession_number} is stored under {len(rows)} registrants; "
                "name one with --cik"
            )
            raise InvalidAcceptanceRequestError(message)
        filing, registrant_cik = rows[0]
        if registrant_cik is None:  # pragma: no cover - company.cik is what resolved the filing
            message = f"the company of {accession_number} has no CIK"
            raise FilingNotStoredError(message)
        return filing, registrant_cik

    def _require_matching_identity(
        self,
        filing: SecFiling,
        registrant_cik: str,
        result: FilingHeaderResult,
        run: RunRecorder,
    ) -> None:
        parsed = result.header
        problems: list[str] = []
        if parsed.accession_number != filing.accession_number:
            problems.append(f"accession {parsed.accession_number}")
        if parsed.form != str(getattr(filing.form, "value", filing.form)):
            problems.append(f"form {parsed.form}")
        if parsed.filing_date != filing.filing_date:
            problems.append(f"filing date {parsed.filing_date}")
        if registrant_cik not in parsed.ciks:
            problems.append("registrant CIK not among the header's filers")
        if problems:
            store_rejected_captures(run, self._store, [result.capture])
            message = (
                f"the EDGAR header does not describe stored filing {filing.accession_number}: "
                + "; ".join(problems)
                + ". accepted_at is left unchanged."
            )
            raise AcceptanceHeaderMismatchError(message)
