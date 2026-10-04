"""Re-run verification (Step 10.9A/B): pure comparison plus a read-only re-assembly.

Answers exactly: *given the currently stored evidence and the effective inputs of a baseline, do we
reproduce the same manifest and the same result?* It does NOT prove that external source bytes (or
any evidence that was not pinned) still exist as they once did, and it persists nothing.

Two baseline kinds are distinguished in every report (``baseline``):

* ``SUPPLIED`` -- a baseline the caller kept (for example one persisted by 10.9B): detects change
  over time;
* ``RERUN`` -- two fresh assemblies of the CURRENT stored state: detects only nondeterminism, and a
  MATCH there says nothing about stability over time.

Status precedence (a detected change always outranks incompleteness):

1. ``MANIFEST_CHANGED`` -- the manifest fingerprints differ (request, methodology or evidence);
2. ``RESULT_CHANGED``   -- same manifest, different result fingerprint;
3. ``EVIDENCE_INCOMPLETE`` -- manifest and result match, but a baseline is ``PARTIAL``: a match on
   evidence that cannot be pinned is not a reproducibility claim;
4. ``MATCH`` -- manifest and result match and both baselines are ``COMPLETE``.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sqlalchemy.orm import Session

from quantforge.financials.eps import EpsKind, EpsPeriodClass
from quantforge.models.enums import ReproducibilityLevel
from quantforge.observation import assemble_research_observation
from quantforge.reproducibility.baseline import ReproducibilityBaseline
from quantforge.reproducibility.manifest import (
    ReproducibilityRecord,
    build_reproducibility_record,
)
from quantforge.research.event_returns import DEFAULT_HORIZONS

__all__ = [
    "VerificationReport",
    "VerificationStatus",
    "assemble_record",
    "compare_records",
    "require_clean_session",
    "verify_baseline",
    "verify_rerun",
]


class VerificationStatus(StrEnum):
    MATCH = "MATCH"
    MANIFEST_CHANGED = "MANIFEST_CHANGED"
    RESULT_CHANGED = "RESULT_CHANGED"
    EVIDENCE_INCOMPLETE = "EVIDENCE_INCOMPLETE"


@dataclass(frozen=True, slots=True)
class VerificationReport:
    status: VerificationStatus
    expected: ReproducibilityBaseline
    actual: ReproducibilityBaseline
    #: Which manifest sections differ: a subset of request / methodology / evidence.
    changed_sections: tuple[str, ...]
    #: Dotted canonical paths inside the manifest that differ (equal-length lists compare by index).
    changed_paths: tuple[str, ...]
    #: Dotted canonical paths inside the RESULT that differ (empty when the results are equal).
    changed_result_paths: tuple[str, ...]
    #: Level of the ACTUAL baseline; PARTIAL means no complete-reproducibility claim is made.
    level: ReproducibilityLevel
    #: SUPPLIED or RERUN (see the module docstring).
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
    if isinstance(left, list) and isinstance(right, list) and len(left) == len(right):
        return [
            path
            for i, (a, b) in enumerate(zip(left, right, strict=True))
            for path in _diff(a, b, f"{prefix}[{i}]")
        ]
    return [] if left == right else [prefix]


def _as_baseline(value: ReproducibilityRecord | ReproducibilityBaseline) -> ReproducibilityBaseline:
    return value.baseline if isinstance(value, ReproducibilityRecord) else value


def compare_records(
    expected: ReproducibilityRecord | ReproducibilityBaseline,
    actual: ReproducibilityRecord | ReproducibilityBaseline,
    *,
    baseline: str = "SUPPLIED",
) -> VerificationReport:
    left, right = _as_baseline(expected), _as_baseline(actual)
    sections = [
        name
        for name in ("request", "methodology", "evidence")
        if getattr(left.fingerprints, name) != getattr(right.fingerprints, name)
    ]
    manifest_differs = left.fingerprints.manifest != right.fingerprints.manifest
    result_differs = left.result_fingerprint != right.result_fingerprint
    manifest_paths = (
        _diff(left.manifest_value, right.manifest_value, "") if manifest_differs else []
    )
    result_paths = _diff(left.result_value, right.result_value, "") if result_differs else []
    if manifest_differs:
        status = VerificationStatus.MANIFEST_CHANGED
    elif result_differs:
        status = VerificationStatus.RESULT_CHANGED
    elif ReproducibilityLevel.PARTIAL in (left.level, right.level):
        status = VerificationStatus.EVIDENCE_INCOMPLETE
    else:
        status = VerificationStatus.MATCH
    return VerificationReport(
        status=status,
        expected=left,
        actual=right,
        changed_sections=tuple(sections),
        changed_paths=tuple(manifest_paths),
        changed_result_paths=tuple(result_paths),
        level=right.level,
        baseline=baseline,
    )


def assemble_record(  # noqa: PLR0913 - mirrors the assembler's explicit inputs
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
) -> ReproducibilityRecord:
    """Assemble the observation and build its reproducibility record (read-only)."""
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


def require_clean_session(session: Session) -> None:
    if session.new or session.dirty or session.deleted:
        message = "this operation requires a clean session (it expires the session between reads)"
        raise ValueError(message)


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
    expected: ReproducibilityRecord | ReproducibilityBaseline | None = None,
) -> VerificationReport:
    """Assemble the observation now and compare it with ``expected``.

    Pass a baseline kept from an earlier computation to detect change over time
    (``baseline == "SUPPLIED"``). Without ``expected`` the call assembles twice against the CURRENT
    stored state (``baseline == "RERUN"``): that only detects nondeterminism, not drift. The session
    is expired between runs so the second run re-reads the stored rows. A session with pending
    changes is refused (expiring it would discard them). Nothing is written or persisted and no
    provider is contacted.
    """
    require_clean_session(session)
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
    kind = "SUPPLIED"
    if expected is None:
        kind = "RERUN"
        expected = assemble_record(session, event_id, **inputs)
    session.expire_all()
    actual = assemble_record(session, event_id, **inputs)
    return compare_records(expected, actual, baseline=kind)


def verify_baseline(session: Session, baseline: ReproducibilityBaseline) -> VerificationReport:
    """Rerun the computation a stored baseline describes and compare, using ITS request.

    The effective inputs come from the baseline's own canonical request (event, horizons, benchmark,
    security, pins, filing accession, EPS selectors), never from current state, so the same question
    is asked again. Methodology and evidence are re-derived now, so a changed methodology label or
    changed consumed evidence shows up as ``MANIFEST_CHANGED``.
    """
    request = baseline.manifest_value["request"]
    return verify_rerun(
        session,
        uuid.UUID(request["event_id"]),
        horizons=tuple(request["horizons"]),
        security_id=_uuid_or_none(request["security_id"]),
        stock_source_id=_uuid_or_none(request["stock_source_id"]),
        benchmark_id=_uuid_or_none(request["benchmark_id"]),
        benchmark_source_id=_uuid_or_none(request["benchmark_source_id"]),
        filing_accession=request["filing_accession"],
        eps_period_class=(
            None
            if request["eps_period_class"] is None
            else EpsPeriodClass(request["eps_period_class"])
        ),
        eps_kind=None if request["eps_kind"] is None else EpsKind(request["eps_kind"]),
        expected=baseline,
    )


def _uuid_or_none(value: str | None) -> uuid.UUID | None:
    return None if value is None else uuid.UUID(value)
