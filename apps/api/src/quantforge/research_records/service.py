"""Save, load and verify immutable research records (Step 10.9B).

Policies (deliberate, tested):

* **Save = assemble, build, confirm, validate, INSERT.** The observation and its reproducibility
  record are assembled TWICE inside the caller's transaction and the two must agree on manifest AND
  result fingerprints before anything is written; otherwise
  :class:`~quantforge.reproducibility.manifest.EvidenceChangedDuringAssemblyError` is raised. That
  detects evidence changing during the save (READ COMMITTED); it is not snapshot isolation, and a
  caller needing that should run the save in a REPEATABLE READ transaction.
* **Exact duplicate (same manifest AND result) is idempotent**: the existing record is returned
  (``created=False``), never a second row.
* **Same manifest, different result is NOT collapsed**: :class:`ResultDivergenceError` carries both
  fingerprints. It signals a methodology-version error, unpinned evidence, data mutation or a bug.
* **Same manifest fingerprint with different canonical content, or content that does not hash to its
  stored digest, is a hard invariant error** (:class:`RecordIntegrityError`), not a user error.
* **PARTIAL records are valid** and saved as such; nothing is ingested to upgrade them. The level and
  gaps are derived from the manifest, never accepted from a caller.
* **No provider, no update, no delete.** The only write is one INSERT of the record.
* **Verification never touches the baseline** and reuses the 10.9A verifier with the STORED baseline;
  it proves current stored evidence still reproduces the baseline, not that unpinned external
  source bytes still exist.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from quantforge.financials.eps import EpsKind, EpsPeriodClass
from quantforge.models.research_record import ResearchObservationRecord
from quantforge.repositories.research_record import ResearchRecordRepository
from quantforge.reproducibility.baseline import ReproducibilityBaseline
from quantforge.reproducibility.canonical import FINGERPRINT_ALGORITHM
from quantforge.reproducibility.manifest import (
    EvidenceChangedDuringAssemblyError,
    ReproducibilityRecord,
)
from quantforge.reproducibility.verifier import (
    VerificationReport,
    assemble_record,
    require_clean_session,
    verify_baseline,
)
from quantforge.research.event_returns import DEFAULT_HORIZONS

__all__ = [
    "RecordIntegrityError",
    "ResearchRecordNotFoundError",
    "ResultDivergenceError",
    "SavedRecord",
    "StoredResearchRecord",
    "load_research_record",
    "save_research_record",
    "verify_stored_record",
]


class RecordIntegrityError(RuntimeError):
    """A stored or about-to-be-stored record violates a hard invariant (hash, canonical form, level)."""


class ResultDivergenceError(RuntimeError):
    """The same manifest now produces a different result than the stored baseline."""

    def __init__(
        self, *, record_id: uuid.UUID, manifest_fingerprint: str, stored: str, current: str
    ) -> None:
        self.record_id = record_id
        self.manifest_fingerprint = manifest_fingerprint
        self.stored_result_fingerprint = stored
        self.current_result_fingerprint = current
        super().__init__(
            f"manifest {manifest_fingerprint[:12]} already has record {record_id} with result "
            f"{stored[:12]}, but the same manifest now yields result {current[:12]}: a "
            "methodology-version error, unpinned evidence, data mutation or a bug. Nothing was saved."
        )


class ResearchRecordNotFoundError(LookupError):
    """No research record has this id."""


@dataclass(frozen=True, slots=True)
class StoredResearchRecord:
    """A validated stored baseline. ``created_at`` is persistence metadata only."""

    record_id: uuid.UUID
    created_at: datetime
    baseline: ReproducibilityBaseline


@dataclass(frozen=True, slots=True)
class SavedRecord:
    record: StoredResearchRecord
    #: False when an identical (manifest, result) baseline already existed and was returned.
    created: bool


def _validated(row: ResearchObservationRecord) -> StoredResearchRecord:
    """Re-derive everything from the stored canonical text and require the row to agree."""
    if (
        row.manifest_hash_algorithm != FINGERPRINT_ALGORITHM
        or row.result_hash_algorithm != FINGERPRINT_ALGORITHM
    ):
        message = f"record {row.id}: unknown hash algorithm"
        raise RecordIntegrityError(message)
    try:
        baseline = ReproducibilityBaseline.from_canonical(
            row.canonical_manifest, row.canonical_result
        )
    except ValueError as exc:
        message = f"record {row.id}: stored canonical content is invalid: {exc}"
        raise RecordIntegrityError(message) from exc
    if baseline.fingerprints.manifest.digest != row.manifest_fingerprint:
        message = f"record {row.id}: the stored manifest does not hash to its stored fingerprint"
        raise RecordIntegrityError(message)
    if baseline.result_fingerprint.digest != row.result_fingerprint:
        message = f"record {row.id}: the stored result does not hash to its stored fingerprint"
        raise RecordIntegrityError(message)
    if [gap.value for gap in baseline.gaps] != list(row.reproducibility_gaps) or (
        baseline.level is not row.reproducibility_level
    ):
        message = f"record {row.id}: the stored level/gaps disagree with the stored manifest"
        raise RecordIntegrityError(message)
    return StoredResearchRecord(row.id, row.created_at, baseline)


def load_research_record(session: Session, record_id: uuid.UUID) -> StoredResearchRecord:
    """Read one record by id and validate it. SELECT only; no recalculation, no provider."""
    row = ResearchRecordRepository(session).get(record_id)
    if row is None:
        message = f"no research record {record_id}"
        raise ResearchRecordNotFoundError(message)
    return _validated(row)


def save_research_record(  # noqa: PLR0913 - mirrors the assembler's explicit inputs
    session: Session,
    event_id: uuid.UUID,
    *,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
    security_id: uuid.UUID | None = None,
    stock_source_id: uuid.UUID | None = None,
    benchmark_id: uuid.UUID | None = None,
    benchmark_source_id: uuid.UUID | None = None,
    filing_accession: str | None = None,
    eps_period_class: EpsPeriodClass | None = None,
    eps_kind: EpsKind | None = None,
) -> SavedRecord:
    """Persist the immutable baseline of one research computation (see the module docstring).

    Nothing is committed here: the INSERT lives in the caller's transaction.
    """
    inputs: dict[str, Any] = {
        "horizons": horizons,
        "security_id": security_id,
        "stock_source_id": stock_source_id,
        "benchmark_id": benchmark_id,
        "benchmark_source_id": benchmark_source_id,
        "filing_accession": filing_accession,
        "eps_period_class": eps_period_class,
        "eps_kind": eps_kind,
    }
    require_clean_session(session)
    first = assemble_record(session, event_id, **inputs)
    session.expire_all()  # the second read must hit the stored rows, not the identity map
    second = assemble_record(session, event_id, **inputs)
    if (
        first.fingerprints.manifest != second.fingerprints.manifest
        or first.result_fingerprint != second.result_fingerprint
    ):
        message = "stored evidence changed between two reads inside this save; nothing was saved"
        raise EvidenceChangedDuringAssemblyError(message)
    return _persist(session, second)


def _persist(session: Session, record: ReproducibilityRecord) -> SavedRecord:
    baseline = record.baseline
    manifest_digest = baseline.fingerprints.manifest.digest
    result_digest = baseline.result_fingerprint.digest
    repository = ResearchRecordRepository(session)

    neighbours = repository.for_manifest(manifest_digest)
    validated = [(row, _validated(row)) for row in neighbours]  # corruption is an invariant error
    for row, stored in validated:
        if stored.baseline.canonical_manifest != baseline.canonical_manifest:
            message = (
                f"manifest fingerprint {manifest_digest[:12]} is stored with different canonical "
                "content: a collision or corruption"
            )
            raise RecordIntegrityError(message)
        if row.result_fingerprint == result_digest:
            return SavedRecord(stored, created=False)  # an exact match anywhere wins
    if neighbours:
        other = neighbours[0]
        raise ResultDivergenceError(
            record_id=other.id,
            manifest_fingerprint=manifest_digest,
            stored=other.result_fingerprint,
            current=result_digest,
        )

    inserted = repository.insert_if_absent(
        {
            "manifest_fingerprint": manifest_digest,
            "manifest_hash_algorithm": baseline.fingerprints.manifest.algorithm,
            "canonical_manifest": baseline.canonical_manifest,
            "result_fingerprint": result_digest,
            "result_hash_algorithm": baseline.result_fingerprint.algorithm,
            "canonical_result": baseline.canonical_result,
            "reproducibility_level": baseline.level,
            "reproducibility_gaps": [gap.value for gap in baseline.gaps],
        }
    )
    persisted = repository.for_pair(manifest_digest, result_digest)
    if persisted is None:  # pragma: no cover - unreachable: the pair was inserted or already held
        message = "the record was reported inserted or present but cannot be read back"
        raise RecordIntegrityError(message)
    return SavedRecord(_validated(persisted), created=inserted is not None)


def verify_stored_record(session: Session, record_id: uuid.UUID) -> VerificationReport:
    """Rerun the computation a stored record describes and compare it with that baseline.

    Uses the 10.9A verifier with the STORED baseline and the baseline's own request. Read-only:
    the record is never updated, and no verification event is persisted.
    """
    stored = load_research_record(session, record_id)
    return verify_baseline(session, stored.baseline)
