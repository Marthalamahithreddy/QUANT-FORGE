"""Ingest accession-bound financial facts from an SEC FSDS package: raw first, bounded, no signal.

Pipeline::

    local FSDS ZIP -> raw store (content-addressed, streamed) -> run manifest
        -> SUB: selected submissions -> bind to EXISTING SecFiling rows -> artifact + submission rows
        -> NUM: streamed, selected accessions only -> batched, idempotent fact writes

This stops at *facts*. No EPS is chosen, no quarter is derived, nothing is compared across
periods, and no availability time is produced.

Design decisions (each is a refusal, not a guess)
-------------------------------------------------
* **SecFiling must already exist.** There is one authoritative filing-ingestion path
  (:mod:`quantforge.ingestion.filings`); creating a filing from an FSDS row would be a second one
  with different acceptance semantics. An explicitly requested accession with no stored filing is a
  :class:`MissingSecFilingError`; a CIK-wide selection reports such accessions as ``unbound`` and
  writes no fact for them. No orphan fact can exist.
* **The company must agree.** The SUB ``cik`` must equal the CIK of the company that owns the stored
  filing. A disagreement (or a company with no CIK, which makes the binding unverifiable) is an
  :class:`AccessionCikMismatchError`. The SUB ``form`` and ``filed`` date must also agree with the
  stored filing. A joint submission stored under several registrants binds to the one whose CIK the
  artifact states.
* **An amendment is its own accession** and therefore its own set of facts; nothing links or
  overwrites by form name.
* **Identical rerun is a skip; a changed value is a conflict.** The fact identity is the FSDS NUM key
  (``value`` is not part of it). A differing value raises :class:`FinancialFactConflictError` and the
  whole run's domain writes roll back.
* **A changed package is a revision, never a replacement.** The same package name arriving with a
  different SHA-256 raises :class:`PackageRevisionError`. The new bytes are preserved in the raw store
  (content-addressed, so the old bytes are untouched) and the failed run records them, but no fact
  from them is written. The SEC re-posted reprocessed data sets in December 2024, so a legitimate
  republication is a real case. **Deliberate safety limitation:** no revision-reconciliation workflow
  exists, so such a package cannot be ingested at all until one is built; the refusal protects the
  stored facts from an accidental local change, it does not "support" SEC revisions.
* **Any rejected selected row fails the run** (the splits policy): a malformed row is never skipped
  while the rest is stored as if the artifact were complete.

Partial runs and absence
------------------------
A CIK-wide run whose selection includes an accession with no stored SecFiling is ``partial``: those
accessions are ``unbound`` and have no facts. ``partial`` (and any run status) says nothing about
coverage: a fact missing from ``financial_fact`` is absent from the *locally ingested accessions
only*, never evidence the source lacks it. Filing metadata comes from ``filings.recent`` only, so
older accessions are unknown locally and cannot be bound until a later ingestion path exists.

Availability
------------
Nothing here sets or implies an availability time. ``SecFiling.accepted_at`` records EDGAR's
acceptance; the bulk package is published later and the SEC does not time-stamp it per package, so a
fact's availability is not ``accepted_at``. ``ingested_at`` is when QuantForge stored the row.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from quantforge.ingestion.canonical import CanonicalFactSubmission, CanonicalFinancialFact
from quantforge.ingestion.raw_store import RawResponseStore
from quantforge.ingestion.runs import RunOutcome, RunRecorder
from quantforge.ingestion.sources import get_or_create_data_source
from quantforge.models.company import Company
from quantforge.models.enums import FinancialFactSourceKind, ProviderType
from quantforge.models.filing import ACCESSION_REGEX, SecFiling
from quantforge.models.financial_fact import (
    FinancialFact,
    FinancialFactArtifact,
    FinancialFactSubmission,
)
from quantforge.providers.sec.client import PROVIDER_NAME
from quantforge.providers.sec.fsds import (
    FsdsNumScan,
    FsdsPackage,
    FsdsSelection,
    FsdsSubmissionScan,
)
from quantforge.providers.sec.identifiers import normalise_cik

__all__ = [
    "AccessionCikMismatchError",
    "AccessionNotInPackageError",
    "FactIngestionResult",
    "FinancialFactConflictError",
    "FinancialFactIngestionService",
    "FsdsRowsRejectedError",
    "InvalidFactRequestError",
    "MissingSecFilingError",
    "NothingToIngestError",
    "PackageRevisionError",
    "SubmissionMetadataMismatchError",
    "UnsupportedFormError",
]

logger = logging.getLogger(__name__)

DATASET = "sec-fsds"
#: Facts are written in batches so a large accession never sits whole in memory.
FACT_BATCH_SIZE = 2000
_PACKAGE_NAME = re.compile(r"[0-9]{4}q[1-4]")


class InvalidFactRequestError(ValueError):
    """The request itself is malformed (checked before any run row is created)."""


class UnsupportedFormError(ValueError):
    """An explicitly requested accession is a form whose facts this adapter does not read."""


class AccessionNotInPackageError(LookupError):
    """A requested accession has no submission row in the package."""


class MissingSecFilingError(LookupError):
    """An explicitly requested accession has no stored SecFiling; none is created from FSDS."""


class NothingToIngestError(LookupError):
    """The selection matched no submission that could be bound to a stored filing."""


class AccessionCikMismatchError(RuntimeError):
    """The package's registrant for an accession is not the company that owns the stored filing."""

    def __init__(
        self, *, accession_number: str, source_cik: str, stored_ciks: list[str | None]
    ) -> None:
        self.accession_number = accession_number
        self.source_cik = source_cik
        self.stored_ciks = stored_ciks
        super().__init__(
            f"{accession_number}: the package states registrant CIK {source_cik}, but the stored "
            f"filing belongs to company CIK(s) {stored_ciks!r} (None = the company has no CIK, so "
            "the binding cannot be verified). Nothing was bound."
        )


class SubmissionMetadataMismatchError(RuntimeError):
    """The package disagrees with the stored filing about its form or filing date."""

    def __init__(self, *, accession_number: str, differences: dict[str, tuple[Any, Any]]) -> None:
        self.accession_number = accession_number
        self.differences = differences
        detail = "; ".join(
            f"{name}: stored={stored!r} package={incoming!r}"
            for name, (stored, incoming) in sorted(differences.items())
        )
        super().__init__(
            f"{accession_number}: the package disagrees with the stored filing ({detail}). The SEC "
            "can correct a submission's metadata after acceptance; decide explicitly which is right."
        )


class PackageRevisionError(RuntimeError):
    """The same package name was seen before with different bytes (a revision of the package)."""

    def __init__(self, *, artifact_name: str, new_sha256: str, known_sha256: list[str]) -> None:
        self.artifact_name = artifact_name
        self.new_sha256 = new_sha256
        self.known_sha256 = known_sha256
        super().__init__(
            f"package {artifact_name!r} was already ingested with different content "
            f"(known sha256 {', '.join(s[:12] for s in known_sha256)}; this file "
            f"{new_sha256[:12]}). The SEC can republish a package (it posted reprocessed data sets "
            "in December 2024), but no revision-reconciliation workflow exists yet, so this "
            "package cannot be ingested. Both byte sets are preserved in the raw store; no fact "
            "from the new bytes was written and none was changed."
        )


class FsdsRowsRejectedError(RuntimeError):
    """Selected rows failed validation; the run stores nothing rather than a partial artifact."""

    def __init__(self, *, count: int, samples: Sequence[str]) -> None:
        self.count = count
        self.samples = tuple(samples)
        super().__init__(
            f"{count} selected FSDS row(s) were rejected; nothing was stored. First: "
            + " | ".join(self.samples[:3])
        )


class FinancialFactConflictError(RuntimeError):
    """A stored fact has the same identity as an incoming one but a different value."""

    def __init__(
        self,
        *,
        accession_number: str,
        identity: tuple[object, ...],
        stored: Decimal | None,
        incoming: Decimal | None,
    ) -> None:
        self.accession_number = accession_number
        self.identity = identity
        self.stored = stored
        self.incoming = incoming
        super().__init__(
            f"{accession_number}: fact {identity!r} is stored as {stored!r} but the package states "
            f"{incoming!r}. The stored fact was not overwritten; nothing from this run was kept."
        )


@dataclass(frozen=True, slots=True)
class FactIngestionResult:
    """What a bounded FSDS ingestion did, beyond the run's counters."""

    outcome: RunOutcome
    artifact_id: uuid.UUID
    bound_accessions: tuple[str, ...]
    #: Selected by CIK, supported form, but no stored SecFiling: no fact written for these.
    unbound_accessions: tuple[str, ...]
    #: Selected by CIK but of a form whose facts are not read: (accession, form).
    out_of_scope_forms: tuple[tuple[str, str], ...]


_Identity = tuple[str, str, str, object, int, str, str, str]


def _identity(fact: CanonicalFinancialFact) -> _Identity:
    return (
        fact.accession_number,
        fact.tag,
        fact.taxonomy_version,
        fact.period_end,
        fact.duration_quarters,
        fact.unit,
        fact.segments,
        fact.coreg,
    )


class FinancialFactIngestionService:
    """Runs one bounded FSDS package ingestion. Synchronous: it reads a local file."""

    __slots__ = ("_now", "_session", "_store")

    def __init__(self, *, session: Session, store: RawResponseStore, now: datetime) -> None:
        self._session = session
        self._store = store
        self._now = now

    def ingest_fsds_package(
        self,
        package: Path,
        *,
        package_name: str,
        accessions: Iterable[str] = (),
        ciks: Iterable[str] = (),
    ) -> FactIngestionResult:
        """Extract the selected submissions' facts from one local FSDS ZIP.

        ``package_name`` is the package's logical identity (``2025q3``); it is not derived from the
        file name here. At least one accession or CIK is required: there is no whole-package or
        whole-archive ingestion.
        """
        selection, explicit = self._validate_request(package, package_name, accessions, ciks)
        with RunRecorder(
            session=self._session,
            store=self._store,
            provider=PROVIDER_NAME,
            dataset=DATASET,
            request_parameters={
                "package": package_name,
                "accessions": sorted(selection.accessions),
                "ciks": sorted(selection.ciks),
            },
            now=self._now,
        ) as run:
            source = get_or_create_data_source(
                self._session, name=PROVIDER_NAME, provider_type=ProviderType.REGULATORY_FILING
            )
            # Raw first, and the package is parsed from the preserved copy, so the facts always
            # come from exactly the bytes whose digest is recorded.
            stored = self._store.store_file(
                package, provider=PROVIDER_NAME, dataset=DATASET, key_segment=package_name
            )
            run.record_file_artifacts([(f"fsds-package:{package_name}", stored)])
            self._refuse_revision(package_name, stored.sha256)

            with FsdsPackage(self._store.path_of(stored.relative_path)) as archive:
                sub_scan = archive.scan_submissions(selection)
                if sub_scan.rejected_count:
                    run.count_failed(sub_scan.rejected_count)
                    raise FsdsRowsRejectedError(
                        count=sub_scan.rejected_count, samples=sub_scan.rejected_samples
                    )
                bound, unbound, out_of_scope = self._bind(sub_scan, explicit)
                has_segments = archive.read_num_layout()
                artifact = self._register_artifact(
                    source_id=source.id,
                    package_name=package_name,
                    sha256=stored.sha256,
                    byte_length=stored.byte_length,
                    raw_path=stored.relative_path,
                    sub_sha256=archive.member_sha256["sub.txt"],
                    num_has_segments=has_segments,
                    run_id=run.row.id,
                )
                submission_ids = self._register_submissions(artifact.id, bound)
                num_scan = FsdsNumScan()
                try:
                    written, skipped = self._write_facts(
                        archive.iter_facts(frozenset(bound), num_scan),
                        scan=num_scan,
                        filing_ids={a: s.filing_id for a, s in bound.items()},
                        submission_ids=submission_ids,
                        run_id=run.row.id,
                    )
                finally:
                    # Facts about the fetch, recorded even when a conflict aborts the stream.
                    run.count_received(num_scan.rows_read)
                    run.count_failed(num_scan.rejected_count)
                if num_scan.rejected_count:
                    raise FsdsRowsRejectedError(
                        count=num_scan.rejected_count, samples=num_scan.rejected_samples
                    )
                self._finish_artifact(artifact, archive.member_sha256["num.txt"])

            run.count_written(written)
            run.count_skipped(skipped)
            outcome = run.partial(self._now) if unbound else run.succeed(self._now)
            logger.info("sec fsds %s: %s", package_name, outcome)
            return FactIngestionResult(
                outcome=outcome,
                artifact_id=artifact.id,
                bound_accessions=tuple(sorted(bound)),
                unbound_accessions=tuple(sorted(unbound)),
                out_of_scope_forms=tuple(sorted(out_of_scope.items())),
            )

    # ── request ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _validate_request(
        package: Path, package_name: str, accessions: Iterable[str], ciks: Iterable[str]
    ) -> tuple[FsdsSelection, frozenset[str]]:
        if not _PACKAGE_NAME.fullmatch(package_name):
            message = f"package name must look like 2025q3, got {package_name!r}"
            raise InvalidFactRequestError(message)
        if not package.is_file():
            message = f"{package.name!r} is not a file"
            raise InvalidFactRequestError(message)
        explicit = frozenset(accessions)
        for accession in explicit:
            if not ACCESSION_REGEX.fullmatch(accession):
                message = f"accession must look like 0000320193-24-000123, got {accession!r}"
                raise InvalidFactRequestError(message)
        try:
            normalised = frozenset(normalise_cik(cik) for cik in ciks)
        except ValueError as exc:
            raise InvalidFactRequestError(str(exc)) from exc
        if not explicit and not normalised:
            message = (
                "name at least one --accession or --cik: whole-package ingestion is not supported"
            )
            raise InvalidFactRequestError(message)
        return FsdsSelection(accessions=explicit, ciks=normalised), explicit

    # ── binding ──────────────────────────────────────────────────────────────────────────

    def _bind(
        self,
        scan: FsdsSubmissionScan,
        explicit: frozenset[str],
    ) -> tuple[dict[str, _Bound], list[str], dict[str, str]]:
        for accession in sorted(explicit):
            if accession in scan.out_of_scope:
                message = (
                    f"{accession} is a {scan.out_of_scope[accession]!r} submission; only 10-K, "
                    "10-K/A, 10-Q and 10-Q/A facts are read"
                )
                raise UnsupportedFormError(message)
            if accession not in scan.submissions:
                message = f"{accession} is not a submission in this package"
                raise AccessionNotInPackageError(message)

        stored = self._stored_filings(list(scan.submissions))
        bound: dict[str, _Bound] = {}
        unbound: list[str] = []
        for accession in sorted(scan.submissions):
            submission: CanonicalFactSubmission = scan.submissions[accession]
            candidates = stored.get(accession, [])
            if not candidates:
                if accession in explicit:
                    message = (
                        f"no stored SecFiling for {accession}; ingest the registrant's filing "
                        "metadata first (a filing is never created from an FSDS row)"
                    )
                    raise MissingSecFilingError(message)
                unbound.append(accession)
                continue
            matching = [c for c in candidates if c.company_cik == submission.cik]
            if not matching:
                raise AccessionCikMismatchError(
                    accession_number=accession,
                    source_cik=submission.cik,
                    stored_ciks=sorted((c.company_cik for c in candidates), key=str),
                )
            filing = matching[0]
            differences = {
                name: pair
                for name, pair in (
                    ("form", (filing.form, submission.form.value)),
                    ("filing_date", (filing.filing_date, submission.filing_date)),
                )
                if pair[0] != pair[1]
            }
            if differences:
                raise SubmissionMetadataMismatchError(
                    accession_number=accession, differences=differences
                )
            bound[accession] = _Bound(filing.filing_id, submission)
        if not bound:
            message = "no selected submission could be bound to a stored filing" + (
                f" (unbound: {', '.join(unbound[:5])})"
                if unbound
                else "; the selection matched no 10-K/10-Q submission"
            )
            raise NothingToIngestError(message)
        return bound, unbound, dict(scan.out_of_scope)

    def _stored_filings(self, accessions: list[str]) -> dict[str, list[_StoredFiling]]:
        if not accessions:
            return {}
        rows = self._session.execute(
            sa.select(
                SecFiling.id,
                SecFiling.accession_number,
                SecFiling.form,
                SecFiling.filing_date,
                Company.cik,
            )
            .join(Company, Company.id == SecFiling.company_id)
            .where(SecFiling.accession_number.in_(accessions))
        )
        found: dict[str, list[_StoredFiling]] = {}
        for filing_id, accession, form, filing_date, cik in rows:
            found.setdefault(accession, []).append(
                _StoredFiling(filing_id, form.value, filing_date, cik)
            )
        return found

    # ── artifact and submissions ─────────────────────────────────────────────────────────

    def _refuse_revision(self, package_name: str, sha256: str) -> None:
        known = list(
            self._session.scalars(
                sa.select(FinancialFactArtifact.sha256).where(
                    FinancialFactArtifact.source_kind == FinancialFactSourceKind.SEC_FSDS,
                    FinancialFactArtifact.artifact_name == package_name,
                    FinancialFactArtifact.sha256 != sha256,
                )
            )
        )
        if known:
            raise PackageRevisionError(
                artifact_name=package_name, new_sha256=sha256, known_sha256=sorted(known)
            )

    def _register_artifact(  # noqa: PLR0913 - one keyword per provenance field
        self,
        *,
        source_id: uuid.UUID,
        package_name: str,
        sha256: str,
        byte_length: int,
        raw_path: str,
        sub_sha256: str,
        num_has_segments: bool,
        run_id: uuid.UUID,
    ) -> FinancialFactArtifact:
        existing = self._session.scalar(
            sa.select(FinancialFactArtifact).where(
                FinancialFactArtifact.source_kind == FinancialFactSourceKind.SEC_FSDS,
                FinancialFactArtifact.artifact_name == package_name,
                FinancialFactArtifact.sha256 == sha256,
            )
        )
        if existing is not None:
            return existing
        artifact = FinancialFactArtifact(
            source_id=source_id,
            source_kind=FinancialFactSourceKind.SEC_FSDS,
            artifact_name=package_name,
            sha256=sha256,
            byte_length=byte_length,
            raw_artifact_path=raw_path,
            # ``num.txt`` is added once NUM has been read in full (see _finish_artifact).
            member_digests={"sub.txt": sub_sha256},
            num_has_segments=num_has_segments,
            ingestion_run_id=run_id,
            ingested_at=self._now,
            created_at=self._now,
        )
        self._session.add(artifact)
        self._session.flush()
        return artifact

    def _finish_artifact(self, artifact: FinancialFactArtifact, num_sha256: str) -> None:
        digests = dict(artifact.member_digests)
        recorded = digests.get("num.txt")
        if recorded is not None and recorded != num_sha256:
            message = (
                f"{artifact.artifact_name}: num.txt digest {num_sha256[:12]} disagrees with the "
                f"stored {recorded[:12]} for the same package digest; the raw copy is corrupt"
            )
            raise RuntimeError(message)
        if recorded is None:
            digests["num.txt"] = num_sha256
            artifact.member_digests = digests  # a new dict, so the JSONB change is detected
            self._session.flush()

    def _register_submissions(
        self, artifact_id: uuid.UUID, bound: dict[str, _Bound]
    ) -> dict[str, uuid.UUID]:
        ids: dict[str, uuid.UUID] = {}
        for accession, item in sorted(bound.items()):
            submission = item.submission
            existing = self._session.scalar(
                sa.select(FinancialFactSubmission.id).where(
                    FinancialFactSubmission.artifact_id == artifact_id,
                    FinancialFactSubmission.sec_filing_id == item.filing_id,
                )
            )
            if existing is not None:
                ids[accession] = existing
                continue
            row = FinancialFactSubmission(
                artifact_id=artifact_id,
                sec_filing_id=item.filing_id,
                period_end=submission.period_end,
                fiscal_year=submission.fiscal_year,
                fiscal_period=submission.fiscal_period,
                fiscal_year_end=submission.fiscal_year_end,
                source_accepted=submission.source_stated_accepted,
                coregistrant_count=submission.coregistrant_count,
                additional_ciks=submission.additional_ciks,
                created_at=self._now,
            )
            self._session.add(row)
            self._session.flush()
            ids[accession] = row.id
        return ids

    # ── facts ────────────────────────────────────────────────────────────────────────────

    def _write_facts(
        self,
        facts: Iterable[CanonicalFinancialFact],
        *,
        scan: FsdsNumScan,
        filing_ids: dict[str, uuid.UUID],
        submission_ids: dict[str, uuid.UUID],
        run_id: uuid.UUID,
    ) -> tuple[int, int]:
        written = skipped = 0
        batch: list[CanonicalFinancialFact] = []
        for fact in facts:
            if scan.rejected_count:
                # A selected row was rejected: the run is already doomed, so stop writing but keep
                # reading, so the received and rejected counts are complete rather than a prefix.
                batch.clear()
                continue
            batch.append(fact)
            if len(batch) >= FACT_BATCH_SIZE:
                w, s = self._write_batch(batch, filing_ids, submission_ids, run_id)
                written, skipped = written + w, skipped + s
                batch = []
        # A rejection on the final rows leaves no later fact to clear the tail, so check again:
        # a doomed run writes nothing more (and a conflict here must not mask the rejection).
        if batch and not scan.rejected_count:
            w, s = self._write_batch(batch, filing_ids, submission_ids, run_id)
            written, skipped = written + w, skipped + s
        return written, skipped

    def _write_batch(
        self,
        batch: list[CanonicalFinancialFact],
        filing_ids: dict[str, uuid.UUID],
        submission_ids: dict[str, uuid.UUID],
        run_id: uuid.UUID,
    ) -> tuple[int, int]:
        unique: dict[_Identity, CanonicalFinancialFact] = {}
        skipped = 0
        for fact in batch:
            key = _identity(fact)
            seen = unique.get(key)
            if seen is None:
                unique[key] = fact
            elif seen.value == fact.value:
                skipped += 1  # the same fact stated twice with the same value
            else:
                raise FinancialFactConflictError(
                    accession_number=fact.accession_number,
                    identity=key[1:],
                    stored=seen.value,
                    incoming=fact.value,
                )

        rows = [
            {
                "id": uuid.uuid4(),
                "sec_filing_id": filing_ids[fact.accession_number],
                "submission_id": submission_ids[fact.accession_number],
                "tag": fact.tag,
                "version": fact.taxonomy_version,
                "period_end": fact.period_end,
                "duration_quarters": fact.duration_quarters,
                "unit": fact.unit,
                "segments": fact.segments,
                "coreg": fact.coreg,
                "value": fact.value,
                "ingestion_run_id": run_id,
                "ingested_at": self._now,
                "created_at": self._now,
            }
            for fact in unique.values()
        ]
        inserted = self._session.execute(
            postgresql.insert(FinancialFact)
            .values(rows)
            .on_conflict_do_nothing(constraint="uq_financial_fact_identity")
            .returning(*_IDENTITY_COLUMNS)
        ).all()
        new_keys = {self._row_key(row, filing_ids) for row in inserted}
        existing_keys = [k for k in unique if k not in new_keys]
        if existing_keys:
            self._compare_existing(existing_keys, unique, filing_ids)
        return len(new_keys), skipped + len(existing_keys)

    @staticmethod
    def _row_key(row: Any, filing_ids: dict[str, uuid.UUID]) -> _Identity:
        accession = next(a for a, f in filing_ids.items() if f == row.sec_filing_id)  # few per run
        return (
            accession,
            row.tag,
            row.version,
            row.period_end,
            row.duration_quarters,
            row.unit,
            row.segments,
            row.coreg,
        )

    def _compare_existing(
        self,
        keys: list[_Identity],
        unique: dict[_Identity, CanonicalFinancialFact],
        filing_ids: dict[str, uuid.UUID],
    ) -> None:
        columns = [FinancialFact.__table__.c[name] for name in _IDENTITY_NAMES]
        wanted = [(filing_ids[k[0]], *k[1:]) for k in keys]
        stored = {
            self._row_key(row, filing_ids): row.value
            for row in self._session.execute(
                sa.select(*columns, FinancialFact.value).where(sa.tuple_(*columns).in_(wanted))
            )
        }
        for key in keys:
            incoming = unique[key].value
            if key not in stored:  # pragma: no cover - unreachable in READ COMMITTED
                message = f"fact {key!r} reported as held but not visible"
                raise RuntimeError(message)
            if stored[key] != incoming:
                raise FinancialFactConflictError(
                    accession_number=key[0], identity=key[1:], stored=stored[key], incoming=incoming
                )


_IDENTITY_NAMES = (
    "sec_filing_id",
    "tag",
    "version",
    "period_end",
    "duration_quarters",
    "unit",
    "segments",
    "coreg",
)
_IDENTITY_COLUMNS = tuple(FinancialFact.__table__.c[name] for name in _IDENTITY_NAMES)


@dataclass(frozen=True, slots=True)
class _StoredFiling:
    filing_id: uuid.UUID
    form: str
    filing_date: Any
    company_cik: str | None


@dataclass(frozen=True, slots=True)
class _Bound:
    filing_id: uuid.UUID
    submission: CanonicalFactSubmission
