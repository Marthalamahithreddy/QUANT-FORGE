"""Ingest SEC filing metadata: raw capture, canonical validation, idempotent write.

Same standards as the other providers: the raw responses are stored before they
are interpreted, a run row is opened first and finalised in the caller's
transaction (a failure rolls back the run's domain writes but keeps a durable
``failed`` run -- the Step 10.4H :class:`~quantforge.ingestion.runs.RunRecorder`
semantics, unchanged), and nothing partial survives.

Identity and conflicts
----------------------
A filing is identified by ``(company, accession)``. Re-ingesting an accession
whose stored metadata is identical is a skip. If the *same* accession now
arrives with different source facts, that is **not** silently overwritten: the
SEC can alter a submission's metadata after acceptance (its dissemination spec
documents a post-acceptance correction that can set a new filing date), and
replacing the stored row would rewrite history under any analysis already run.
It raises :class:`SecFilingConflictError`, naming the fields, for an operator to
decide. Amendments are new accessions, hence new rows; they never touch the
original.

The registrant is resolved through ``company.cik``; a CIK no stored company
carries is an explicit failure -- a company is never created from SEC data.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from quantforge.ingestion.canonical import CanonicalSecFiling
from quantforge.ingestion.raw_store import RawResponseStore
from quantforge.ingestion.runs import FAILED_RUN_RECORDED, RunOutcome, RunRecorder
from quantforge.ingestion.service import store_rejected_captures
from quantforge.ingestion.sources import get_or_create_data_source
from quantforge.models.company import Company
from quantforge.models.enums import ProviderType, SecFormType
from quantforge.models.filing import SecFiling
from quantforge.models.ingestion import IngestionRun
from quantforge.providers.errors import ProviderError
from quantforge.providers.filings import FilingProvider

__all__ = [
    "AllFilingRecordsRejectedError",
    "FilingIngestionService",
    "SecFilingConflictError",
    "SecFilingIngestor",
    "UnknownRegistrantError",
    "conflicting_filing_fields",
]

logger = logging.getLogger(__name__)

_COMPARED_FIELDS = (
    "form",
    "filing_date",
    "report_date",
    "accepted_at",
    "primary_document",
    "items",
)


class AllFilingRecordsRejectedError(RuntimeError):
    """Every supported-form record in a response was rejected: a systematic problem, not noise.

    A run where *nothing* could be stored is a failure, not a partial success --
    otherwise a wrong timestamp reading or a changed SEC format would look like a
    successful run that stored no filings.
    """


class UnknownRegistrantError(LookupError):
    """No stored company carries this CIK; a company is never created from SEC data."""


class SecFilingConflictError(RuntimeError):
    """A stored filing disagrees with the incoming metadata for the same accession."""

    def __init__(self, *, accession_number: str, differences: dict[str, tuple[Any, Any]]) -> None:
        self.accession_number = accession_number
        self.differences = differences
        detail = "; ".join(
            f"{field}: stored={stored!r} incoming={incoming!r}"
            for field, (stored, incoming) in sorted(differences.items())
        )
        super().__init__(
            f"{accession_number}: stored filing metadata differs from the incoming "
            f"({detail}). The existing row is left untouched; the SEC can correct a "
            "submission's metadata after acceptance, so decide explicitly whether to keep "
            "the stored value or restate it."
        )


def conflicting_filing_fields(
    stored: SecFiling, incoming: CanonicalSecFiling
) -> dict[str, tuple[Any, Any]]:
    """Fields where the incoming metadata disagrees with the stored row."""
    differences: dict[str, tuple[Any, Any]] = {}
    for name in _COMPARED_FIELDS:
        stored_value = getattr(stored, name)
        incoming_value = getattr(incoming, name)
        if isinstance(stored_value, SecFormType):
            stored_value = stored_value.value
        if isinstance(incoming_value, SecFormType):
            incoming_value = incoming_value.value
        if stored_value != incoming_value:
            differences[name] = (stored_value, incoming_value)
    return differences


class SecFilingIngestor:
    """Writes canonical filings for one registrant, idempotently."""

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
        filings: Sequence[CanonicalSecFiling],
        *,
        company_id: uuid.UUID,
        source_id: uuid.UUID,
    ) -> tuple[int, int]:
        """Insert new filings, skip identical ones; return ``(written, skipped)``.

        Raises:
            SecFilingConflictError: a stored accession's metadata differs.
        """
        written = skipped = 0
        for filing in sorted(filings, key=lambda f: (f.accepted_at, f.accession_number)):
            if self._insert_if_absent(company_id=company_id, source_id=source_id, filing=filing):
                written += 1
                continue
            existing = self._session.scalar(
                sa.select(SecFiling).where(
                    SecFiling.company_id == company_id,
                    SecFiling.accession_number == filing.accession_number,
                )
            )
            if existing is None:  # pragma: no cover - unreachable in READ COMMITTED
                message = f"accession {filing.accession_number} reported as held but not visible"
                raise RuntimeError(message)
            differences = conflicting_filing_fields(existing, filing)
            if differences:
                raise SecFilingConflictError(
                    accession_number=filing.accession_number, differences=differences
                )
            skipped += 1
        return written, skipped

    def _insert_if_absent(
        self, *, company_id: uuid.UUID, source_id: uuid.UUID, filing: CanonicalSecFiling
    ) -> bool:
        statement = postgresql.insert(SecFiling).values(
            id=uuid.uuid4(),
            company_id=company_id,
            accession_number=filing.accession_number,
            form=filing.form.value,
            filing_date=filing.filing_date,
            report_date=filing.report_date,
            accepted_at=filing.accepted_at,
            primary_document=filing.primary_document,
            items=filing.items,
            source_id=source_id,
            ingestion_run_id=self._run_id,
            ingested_at=self._now,
            created_at=self._now,
        )
        result = self._session.execute(
            statement.on_conflict_do_nothing(
                index_elements=["company_id", "accession_number"]
            ).returning(SecFiling.id)
        )
        return result.scalar_one_or_none() is not None


class FilingIngestionService:
    """Runs SEC filing-metadata ingestion against a :class:`FilingProvider`."""

    __slots__ = ("_now", "_provider", "_session", "_store")

    def __init__(
        self,
        *,
        session: Session,
        provider: FilingProvider,
        store: RawResponseStore,
        now: datetime,
    ) -> None:
        self._session = session
        self._provider = provider
        self._store = store
        self._now = now

    async def ingest_filings(self, cik: str) -> RunOutcome:
        """Fetch and store filing metadata for the stored company that carries ``cik``."""
        self._session.info.pop(FAILED_RUN_RECORDED, None)
        with RunRecorder(
            session=self._session,
            store=self._store,
            provider=self._provider.name,
            dataset="sec-filings",
            request_parameters={"cik": cik},
            now=self._now,
        ) as run:
            source = get_or_create_data_source(
                self._session,
                name=self._provider.name,
                provider_type=ProviderType.REGULATORY_FILING,
            )
            company_id = self._session.scalar(sa.select(Company.id).where(Company.cik == cik))
            if company_id is None:
                message = f"no stored company has CIK {cik}; ingest the company's reference first"
                raise UnknownRegistrantError(message)

            try:
                result = await self._provider.get_filings(cik)
            except ProviderError as exc:
                store_rejected_captures(run, self._store, exc.captures)
                raise
            failed = len(result.rejected)
            for reason in result.rejected:
                logger.warning("rejected SEC filing record for CIK %s: %s", cik, reason)
            if failed and not result.filings:
                # Nothing could be stored: a systematic problem, not noise. Record the
                # counts first so the durable failed run is queryable, and keep the
                # response as rejected evidence -- never as an accepted artifact.
                run.count_received(failed)
                run.count_failed(failed)
                store_rejected_captures(run, self._store, result.captures)
                message = (
                    f"all {failed} supported-form record(s) for CIK {cik} were rejected; first: "
                    f"{result.rejected[0]}"
                )
                raise AllFilingRecordsRejectedError(message)
            if result.captures:
                run.record_captures(self._store.store_all(result.captures))

            ingestor = SecFilingIngestor(self._session, now=self._now, run=run.row)
            written, skipped = ingestor.ingest(
                result.filings, company_id=company_id, source_id=source.id
            )
            run.count_received(len(result.filings) + failed)
            run.count_written(written)
            run.count_skipped(skipped)
            run.count_failed(failed)
            outcome = run.partial(self._now) if failed else run.succeed(self._now)
            logger.info("sec filings for CIK %s: %s", cik, outcome)
            return outcome
