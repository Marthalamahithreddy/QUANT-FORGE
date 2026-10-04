"""Public schemas for the read-only persisted research record endpoints (Step 11.0A).

A record is the immutable BASELINE of one completed research computation (Step 10.9B). These
shapes expose it for review and re-verification; there is no schema for creating one, because saving
stays an internal service.

* The list carries stored columns plus a small request summary, never the canonical payloads.
* The detail carries the parsed manifest and result (structured JSON, financial values already
  fixed-point strings, no float anywhere) plus the fingerprints and the derived level and gaps. The
  canonical TEXT the fingerprints were taken over is integrity infrastructure and is not exposed.
* ``created_at`` is persistence metadata (when the record was saved), never a data availability time.
* ``manifest`` and ``result`` are the stored domain structures of that computation, versioned by the
  manifest's own ``methodology.manifest_schema``; they are deliberately untyped objects, not a
  second public contract (use ``GET /research/observations/{event_id}`` for the typed observation).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, Field

from quantforge.models.enums import ReproducibilityGap, ReproducibilityLevel

__all__ = [
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "MAX_OFFSET",
    "NoQuery",
    "RecordFingerprints",
    "RecordRequestSummary",
    "RecordVerificationResponse",
    "RecordVerificationSide",
    "ResearchRecordDetail",
    "ResearchRecordListQuery",
    "ResearchRecordPage",
    "ResearchRecordSummary",
]

# The same bounds as the earnings and filing lists, so clients paginate one way everywhere.
DEFAULT_LIMIT: Final = 50
MAX_LIMIT: Final = 200
MAX_OFFSET: Final = 1_000_000

Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class NoQuery(BaseModel):
    """A route that takes no query parameters: any supplied one is a 422, never silently ignored."""

    model_config = {"extra": "forbid"}


class ResearchRecordListQuery(BaseModel):
    """Pagination only. Unknown parameters are rejected."""

    model_config = {"extra": "forbid"}

    limit: int = Field(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT)
    offset: int = Field(0, ge=0, le=MAX_OFFSET)


class RecordRequestSummary(BaseModel):
    """What was asked, as stored in the record's manifest (the effective inputs after defaults)."""

    event_id: uuid.UUID
    horizons: list[int]
    benchmark_id: uuid.UUID | None
    security_id: uuid.UUID | None
    filing_accession: str | None
    #: The stored EPS selector values as text (the financials layer is not imported here).
    eps_period_class: str | None
    eps_kind: str | None


class ResearchRecordSummary(BaseModel):
    record_id: uuid.UUID = Field(description="The identity of this persisted record.")
    created_at: datetime = Field(
        description="When this baseline was saved (persistence metadata, not data availability)."
    )
    hash_algorithm: Literal["sha256"] = Field(
        description=(
            "The algorithm of both fingerprints. The stored text is re-hashed on get and verify, "
            "not when listing."
        )
    )
    manifest_fingerprint: Sha256Hex = Field(
        description="Identity of the request, methodology and consumed evidence (not of the record)."
    )
    result_fingerprint: Sha256Hex = Field(description="Identity of the produced observation.")
    reproducibility_level: ReproducibilityLevel = Field(
        description="COMPLETE or PARTIAL, derived from the gaps at save time; never chosen."
    )
    reproducibility_gaps: list[ReproducibilityGap] = Field(
        description="Why the record is PARTIAL (empty when COMPLETE)."
    )
    request: RecordRequestSummary


class ResearchRecordPage(BaseModel):
    items: list[ResearchRecordSummary]
    total: int = Field(description="Stored records, before pagination.")
    limit: int
    offset: int


class ResearchRecordDetail(ResearchRecordSummary):
    manifest: dict[str, Any] = Field(
        description="The stored manifest (request, methodology, evidence), parsed."
    )
    result: dict[str, Any] = Field(description="The stored result, parsed. Decimals are strings.")


class RecordFingerprints(BaseModel):
    request: Sha256Hex
    methodology: Sha256Hex
    evidence: Sha256Hex
    manifest: Sha256Hex
    result: Sha256Hex


class RecordVerificationSide(BaseModel):
    reproducibility_level: ReproducibilityLevel
    reproducibility_gaps: list[ReproducibilityGap]
    fingerprints: RecordFingerprints


class RecordVerificationResponse(BaseModel):
    """The outcome of re-running a stored record against the CURRENT stored evidence. Nothing is saved."""

    record_id: uuid.UUID
    status: Literal["MATCH", "MANIFEST_CHANGED", "RESULT_CHANGED", "EVIDENCE_INCOMPLETE"] = Field(
        description=(
            "MANIFEST_CHANGED outranks RESULT_CHANGED, which outranks EVIDENCE_INCOMPLETE (the "
            "match is genuine but the baseline or rerun is PARTIAL); MATCH only when both are "
            "COMPLETE. MATCH proves current stored evidence reproduces the baseline, not that "
            "unpinned external source bytes still exist."
        )
    )
    baseline: Literal["SUPPLIED", "RERUN"] = Field(
        description="SUPPLIED: compared with this stored record (always so for this endpoint)."
    )
    reproducibility_level: ReproducibilityLevel = Field(description="Level of the rerun (actual).")
    changed_sections: list[Literal["request", "methodology", "evidence"]]
    changed_paths: list[str] = Field(description="Dotted paths inside the manifest that differ.")
    changed_result_paths: list[str] = Field(
        description="Dotted paths inside the result that differ."
    )
    expected: RecordVerificationSide = Field(description="The stored baseline.")
    actual: RecordVerificationSide = Field(description="The rerun against current stored data.")
