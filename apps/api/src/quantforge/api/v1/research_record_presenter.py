"""Serialisation of persisted research records to the wire (Step 11.0A). No logic beyond mapping.

Nothing is recomputed here: the list is built from stored columns, the detail from a baseline the
load service already re-hashed, and the verification from the 10.9A verifier's own report. Financial
values arrive as the canonical fixed-point strings and are passed through untouched (no Decimal or
float conversion takes place in this module).
"""

from __future__ import annotations

import uuid

from quantforge.repositories.research_record import RecordListRow
from quantforge.reproducibility.baseline import ReproducibilityBaseline
from quantforge.reproducibility.verifier import VerificationReport
from quantforge.research_records import StoredResearchRecord
from quantforge.schemas.research_record import (
    RecordFingerprints,
    RecordRequestSummary,
    RecordVerificationResponse,
    RecordVerificationSide,
    ResearchRecordDetail,
    ResearchRecordSummary,
)

__all__ = ["present_detail", "present_summary_row", "present_verification"]


def present_summary_row(row: RecordListRow) -> ResearchRecordSummary:
    return ResearchRecordSummary.model_validate(
        {
            "record_id": row.record_id,
            "created_at": row.created_at,
            "hash_algorithm": row.manifest_hash_algorithm,
            "manifest_fingerprint": row.manifest_fingerprint,
            "result_fingerprint": row.result_fingerprint,
            "reproducibility_level": row.level,
            "reproducibility_gaps": row.gaps,
            "request": RecordRequestSummary.model_validate(row.request),
        }
    )


def present_detail(stored: StoredResearchRecord) -> ResearchRecordDetail:
    baseline = stored.baseline
    manifest = baseline.manifest_value
    return ResearchRecordDetail.model_validate(
        {
            "record_id": stored.record_id,
            "created_at": stored.created_at,
            "hash_algorithm": baseline.fingerprints.manifest.algorithm,
            "manifest_fingerprint": baseline.fingerprints.manifest.digest,
            "result_fingerprint": baseline.result_fingerprint.digest,
            "reproducibility_level": baseline.level,
            "reproducibility_gaps": list(baseline.gaps),
            "request": RecordRequestSummary.model_validate(manifest["request"]),
            "manifest": manifest,
            "result": baseline.result_value,
        }
    )


def _side(baseline: ReproducibilityBaseline) -> RecordVerificationSide:
    prints = baseline.fingerprints
    return RecordVerificationSide(
        reproducibility_level=baseline.level,
        reproducibility_gaps=list(baseline.gaps),
        fingerprints=RecordFingerprints(
            request=prints.request.digest,
            methodology=prints.methodology.digest,
            evidence=prints.evidence.digest,
            manifest=prints.manifest.digest,
            result=baseline.result_fingerprint.digest,
        ),
    )


def present_verification(
    record_id: uuid.UUID, report: VerificationReport
) -> RecordVerificationResponse:
    return RecordVerificationResponse.model_validate(
        {
            "record_id": record_id,
            "status": report.status.value,
            "baseline": report.baseline,
            "reproducibility_level": report.level,
            "changed_sections": list(report.changed_sections),
            "changed_paths": list(report.changed_paths),
            "changed_result_paths": list(report.changed_result_paths),
            "expected": _side(report.expected),
            "actual": _side(report.actual),
        }
    )
