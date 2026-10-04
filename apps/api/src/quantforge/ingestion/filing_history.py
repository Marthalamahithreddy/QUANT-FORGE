"""Bounded historical SEC filing-IDENTITY ingestion for one explicit registrant.

Why this exists
---------------
``filings.recent`` covers roughly the latest year (or 1,000 filings). An older
accession-bound FSDS fact therefore has no stored :class:`~quantforge.models.filing.SecFiling`
to bind to. This service stores the identity metadata of those older filings -- the
same canonical contract, the same ``(company, accession)`` identity, the same
conservative conflict rules as :mod:`quantforge.ingestion.filings` -- by following the
history files the SEC's own parent response names, for ONE explicit CIK.

What it is not
--------------
* **Not availability.** ``accepted_at`` stays filing-acceptance metadata. Membership in a
  history file, a file's date range and ``retrieved_at`` (QuantForge's own retrieval
  time) say nothing about when a filing became public. No ``observed_at``,
  ``known_as_of`` or ``filing_public_at`` is created, and no availability basis is touched.
* **Not coverage.** The report counts what was looked at; it never says the registrant's
  history is complete, and a requested accession that is not found is "unresolved in the
  fetched scope", not "absent from the SEC".
* **Not fact ingestion.** Nothing here reads FSDS or financial facts; binding happens
  later, explicitly, through the existing FSDS path.
* **Not a crawler.** One explicit CIK per run; one parent request plus at most an
  explicit bound of history-file requests (see ``SecClient.get_filing_history``).

Provenance and idempotence
--------------------------
Every response is stored before it is interpreted, content-addressed by SHA-256 in the
existing raw store, and listed in the run manifest (path, SHA-256, byte length, QuantForge
retrieval time, and for a history file its concrete request path). The run row carries the
manifest's path/digest/size and the request parameters (CIK and scope). Identical bytes
re-fetched are deduplicated; a changed body under the same URL is a *new* artifact beside
the old one -- the SEC documents no immutability for these files, so nothing is
overwritten and no revision is declared "correct". Stored filings are never rewritten:
identical metadata is a skip, differing metadata (including a different ``accepted_at``,
for instance a row written before the 10.7A.1 timestamp correction) is a loud
:class:`~quantforge.ingestion.filings.SecFilingConflictError` for an operator to decide.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Session

from quantforge.ingestion.filings import (
    AllFilingRecordsRejectedError,
    SecFilingIngestor,
    UnknownRegistrantError,
)
from quantforge.ingestion.raw_store import RawResponseStore
from quantforge.ingestion.runs import FAILED_RUN_RECORDED, RunOutcome, RunRecorder
from quantforge.ingestion.service import store_rejected_captures
from quantforge.ingestion.sources import get_or_create_data_source
from quantforge.models.company import Company
from quantforge.models.enums import ProviderType
from quantforge.models.filing import ACCESSION_REGEX
from quantforge.providers.errors import ProviderError
from quantforge.providers.filings import FilingHistoryProvider, FilingHistoryResult
from quantforge.providers.sec.history import MAX_HISTORY_FILES_CEILING

__all__ = [
    "DEFAULT_MAX_HISTORY_FILES",
    "FilingHistoryIngestionService",
    "FilingHistoryReport",
    "InvalidHistoryRequestError",
]

logger = logging.getLogger(__name__)

#: A registrant naming more history files than this is refused up front, never truncated.
DEFAULT_MAX_HISTORY_FILES = 20
_MAX_TARGET_ACCESSIONS = 200


class InvalidHistoryRequestError(ValueError):
    """The history request is malformed (a bad accession or bound); nothing was requested."""


@dataclass(frozen=True, slots=True)
class FilingHistoryReport:
    """What one history run did, without implying complete SEC coverage.

    ``inserted`` / ``matched_existing`` are about stored rows (a re-run of identical
    metadata is all ``matched_existing``); everything else is what was looked at. A
    metadata conflict is not a count: it fails the run (see the module docstring).
    """

    outcome: RunOutcome
    result: FilingHistoryResult
    inserted: int
    matched_existing: int

    @property
    def complete_for_requested_scope(self) -> bool:
        """No record rejected and every requested accession stored. Not SEC coverage."""
        return (
            not self.result.rejected
            and not self.result.unresolved_accessions
            and not self.result.located_not_stored
        )


class FilingHistoryIngestionService:
    """Runs bounded historical filing-identity ingestion against a history-capable provider."""

    __slots__ = ("_now", "_provider", "_session", "_store")

    def __init__(
        self,
        *,
        session: Session,
        provider: FilingHistoryProvider,
        store: RawResponseStore,
        now: datetime,
    ) -> None:
        self._session = session
        self._provider = provider
        self._store = store
        self._now = now

    async def ingest_filing_history(
        self,
        cik: str,
        *,
        accessions: Sequence[str] = (),
        max_history_files: int = DEFAULT_MAX_HISTORY_FILES,
    ) -> FilingHistoryReport:
        """Fetch and store filing identity for the stored company carrying ``cik``."""
        targets = _validated_targets(accessions)
        if not 1 <= max_history_files <= MAX_HISTORY_FILES_CEILING:
            message = f"max_history_files must be between 1 and {MAX_HISTORY_FILES_CEILING}"
            raise InvalidHistoryRequestError(message)
        self._session.info.pop(FAILED_RUN_RECORDED, None)
        with RunRecorder(
            session=self._session,
            store=self._store,
            provider=self._provider.name,
            dataset="sec-filing-history",
            request_parameters={
                "cik": cik,
                "scope": "accessions" if targets else "all-referenced-history-files",
                "accessions": list(targets),
                "max_history_files": max_history_files,
            },
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
                result = await self._provider.get_filing_history(
                    cik, accessions=targets, max_history_files=max_history_files
                )
            except ProviderError as exc:
                store_rejected_captures(run, self._store, exc.captures)
                raise
            failed = len(result.rejected)
            for reason in result.rejected:
                logger.warning("rejected SEC filing record for CIK %s: %s", cik, reason)
            if failed and not result.filings:
                run.count_received(failed)
                run.count_failed(failed)
                store_rejected_captures(run, self._store, result.captures)
                message = (
                    f"all {failed} supported-form record(s) for CIK {cik} were rejected; first: "
                    f"{result.rejected[0]}"
                )
                raise AllFilingRecordsRejectedError(message)
            run.record_captures(self._store.store_all(result.captures))

            ingestor = SecFilingIngestor(self._session, now=self._now, run=run.row)
            inserted, matched = ingestor.ingest(
                result.filings, company_id=company_id, source_id=source.id
            )
            run.count_received(len(result.filings) + failed)
            run.count_written(inserted)
            run.count_skipped(matched)
            run.count_failed(failed)
            incomplete = bool(failed or result.unresolved_accessions or result.located_not_stored)
            outcome = run.partial(self._now) if incomplete else run.succeed(self._now)
            logger.info("sec filing history for CIK %s: %s", cik, outcome)
            return FilingHistoryReport(
                outcome=outcome, result=result, inserted=inserted, matched_existing=matched
            )


def _validated_targets(accessions: Sequence[str]) -> tuple[str, ...]:
    targets = tuple(dict.fromkeys(accessions))
    if len(targets) > _MAX_TARGET_ACCESSIONS:
        message = f"at most {_MAX_TARGET_ACCESSIONS} target accessions per run"
        raise InvalidHistoryRequestError(message)
    for accession in targets:
        if not ACCESSION_REGEX.fullmatch(accession):
            message = f"{accession!r} is not a dashed accession (0000320193-24-000123)"
            raise InvalidHistoryRequestError(message)
    return targets
