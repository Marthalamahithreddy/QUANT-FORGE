"""Re-run verification (Step 10.9A): pure comparison plus a read-only re-assembly.

Answers exactly: *given the currently stored evidence and the same effective inputs, do we
reproduce the same manifest and the same result?* It does NOT prove that the database still holds
the bytes that existed at the original real-world time, and it persists nothing.

Status precedence (a detected change always outranks incompleteness):

1. ``MANIFEST_CHANGED`` -- the manifest fingerprints differ (request, methodology or evidence);
2. ``RESULT_CHANGED``   -- same manifest, different result fingerprint;
3. ``EVIDENCE_INCOMPLETE`` -- manifest and result match, but a record is ``PARTIAL``: a match on
   evidence that cannot be pinned is not a reproducibility claim;
4. ``MATCH`` -- manifest and result match and both records are ``COMPLETE``.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sqlalchemy.orm import Session

from quantforge.financials.eps import EpsKind, EpsPeriodClass
from quantforge.observation import assemble_research_observation
from quantforge.reproducibility.canonical import canonical_value
from quantforge.reproducibility.manifest import (
    ReproducibilityLevel,
    ReproducibilityRecord,
    build_reproducibility_record,
)
from quantforge.research.event_returns import DEFAULT_HORIZONS

__all__ = ["VerificationReport", "VerificationStatus", "compare_records", "verify_rerun"]


class VerificationStatus(StrEnum):
    MATCH = "MATCH"
    MANIFEST_CHANGED = "MANIFEST_CHANGED"
    RESULT_CHANGED = "RESULT_CHANGED"
    EVIDENCE_INCOMPLETE = "EVIDENCE_INCOMPLETE"


@dataclass(frozen=True, slots=True)
class VerificationReport:
    status: VerificationStatus
    expected: ReproducibilityRecord
    actual: ReproducibilityRecord
    #: Which manifest sections differ: a subset of request / methodology / evidence.
    changed_sections: tuple[str, ...]
    #: Dotted canonical paths inside the manifest that differ (lists are compared whole).
    changed_paths: tuple[str, ...]
    #: Level of the ACTUAL record; PARTIAL means no complete-reproducibility claim is made.
    level: ReproducibilityLevel
    #: SUPPLIED: compared against a baseline the caller kept (detects change over time);
    #: RERUN: two fresh assemblies of the CURRENT stored state (detects only nondeterminism).
    baseline: str


def _diff(left: Any, right: Any, prefix: str) -> list[str]:
    if isinstance(left, dict) and isinstance(right, dict):
        paths: list[str] = []
        for key in sorted(set(left) | set(right)):
            child = f"{prefix}.{key}" if prefix else key
            if key not in left or key not in right:
                paths.append(child)
            else:
                paths.extend(_diff(left[key], right[key], child))
        return paths
    return [] if left == right else [prefix]


def compare_records(
    expected: ReproducibilityRecord, actual: ReproducibilityRecord, *, baseline: str = "SUPPLIED"
) -> VerificationReport:
    sections: list[str] = []
    for name in ("request", "methodology", "evidence"):
        if getattr(expected.fingerprints, name) != getattr(actual.fingerprints, name):
            sections.append(name)
    paths = (
        _diff(
            canonical_value(expected.manifest),
            canonical_value(actual.manifest),
            "",
        )
        if expected.fingerprints.manifest != actual.fingerprints.manifest
        else []
    )
    if expected.fingerprints.manifest != actual.fingerprints.manifest:
        status = VerificationStatus.MANIFEST_CHANGED
    elif expected.result_fingerprint != actual.result_fingerprint:
        status = VerificationStatus.RESULT_CHANGED
    elif (
        expected.level is ReproducibilityLevel.PARTIAL
        or actual.level is ReproducibilityLevel.PARTIAL
    ):
        status = VerificationStatus.EVIDENCE_INCOMPLETE
    else:
        status = VerificationStatus.MATCH
    return VerificationReport(
        status=status,
        expected=expected,
        actual=actual,
        changed_sections=tuple(sections),
        changed_paths=tuple(paths),
        level=actual.level,
        baseline=baseline,
    )


def _reproduce(  # noqa: PLR0913 - mirrors the assembler's explicit inputs
    session: Session,
    event_id: uuid.UUID,
    *,
    horizons: Sequence[int],
    security_id: uuid.UUID | None,
    stock_source_id: uuid.UUID | None,
    benchmark_id: uuid.UUID | None,
    benchmark_source_id: uuid.UUID | None,
    filing_accession: str | None,
    eps_period_class: EpsPeriodClass | None,
    eps_kind: EpsKind | None,
) -> ReproducibilityRecord:
    observation = assemble_research_observation(
        session,
        event_id,
        horizons=horizons,
        security_id=security_id,
        stock_source_id=stock_source_id,
        benchmark_id=benchmark_id,
        benchmark_source_id=benchmark_source_id,
        filing_accession=filing_accession,
        eps_period_class=eps_period_class,
        eps_kind=eps_kind,
    )
    return build_reproducibility_record(session, observation)


def verify_rerun(  # noqa: PLR0913 - mirrors the assembler's explicit inputs
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
    expected: ReproducibilityRecord | None = None,
) -> VerificationReport:
    """Assemble the observation now and compare it with ``expected``.

    Pass the ``ReproducibilityRecord`` kept from an earlier computation to detect change over time
    (``baseline == "SUPPLIED"``). Without ``expected`` the call assembles twice against the CURRENT
    stored state (``baseline == "RERUN"``): that only detects nondeterminism, not drift, and a
    MATCH there is not evidence that anything is stable over time. The session is expired between
    runs so the second run re-reads the stored rows. A session with pending changes is refused (expiring it would discard
    them). Nothing is written or persisted and no provider is contacted.
    """
    if session.new or session.dirty or session.deleted:
        message = "the verifier requires a clean session"
        raise ValueError(message)
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
    baseline = "SUPPLIED"
    if expected is None:
        baseline = "RERUN"
        expected = _reproduce(session, event_id, **inputs)
    session.expire_all()
    actual = _reproduce(session, event_id, **inputs)
    return compare_records(expected, actual, baseline=baseline)
