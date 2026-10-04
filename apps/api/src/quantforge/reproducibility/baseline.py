"""A persisted-or-computed baseline, derived ONLY from canonical text (Step 10.9B).

A baseline is what a later rerun is compared against. It is built from the exact canonical manifest
and result texts, so a freshly computed record and a record loaded from the database go through the
same code: fingerprints are re-derived from the text, the text is checked to BE canonical, and the
gaps (hence the level) are derived from the manifest's evidence section. Nothing is trusted from a
caller-supplied level or digest.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Final

from quantforge.models.enums import ReproducibilityGap, ReproducibilityLevel
from quantforge.reproducibility.canonical import (
    Fingerprint,
    canonical_text,
    fingerprint_of,
    fingerprint_of_text,
)

__all__ = [
    "ManifestFingerprints",
    "ReproducibilityBaseline",
    "gaps_from_evidence",
]

_PINNED: Final = "PINNED"
_REQUIRED_SECTIONS: Final = ("request", "methodology", "evidence")


@dataclass(frozen=True, slots=True)
class ManifestFingerprints:
    request: Fingerprint
    methodology: Fingerprint
    evidence: Fingerprint
    #: Covers request + methodology + evidence (not the level, gaps or the result).
    manifest: Fingerprint


def gaps_from_evidence(evidence: dict[str, Any]) -> tuple[ReproducibilityGap, ...]:
    """The reproducibility gaps of a CANONICAL evidence section (the single implementation).

    Derived from what the evidence says was consumed: an item that was never read adds no gap, and a
    consumed item without an ingestion run (or an unpinned absence) does.
    """
    gaps: list[ReproducibilityGap] = []
    if evidence["event"]["ingestion_run_id"] is None:
        gaps.append(ReproducibilityGap.EVENT_WITHOUT_INGESTION_RUN)
    if any(bar["ingestion_run_id"] is None for bar in evidence["stock"]["bars"]):
        gaps.append(ReproducibilityGap.STOCK_BAR_WITHOUT_INGESTION_RUN)
    if any(bar["ingestion_run_id"] is None for bar in evidence["benchmark"]["bars"]):
        gaps.append(ReproducibilityGap.BENCHMARK_BARS_WITHOUT_INGESTION_RUN)
    filing = evidence["filing"]
    if filing["state"] == _PINNED and filing["ingestion_run_id"] is None:
        gaps.append(ReproducibilityGap.FILING_WITHOUT_INGESTION_RUN)
    accounting = evidence["accounting"]
    if accounting["state"] == _PINNED and not accounting["artifacts"]:
        gaps.append(ReproducibilityGap.ACCOUNTING_NOT_INGESTED)
    return tuple(gaps)


@dataclass(frozen=True, slots=True)
class ReproducibilityBaseline:
    """Canonical texts plus everything derived from them. Build it with :meth:`from_canonical`."""

    canonical_manifest: str
    canonical_result: str
    fingerprints: ManifestFingerprints
    result_fingerprint: Fingerprint
    gaps: tuple[ReproducibilityGap, ...]

    @property
    def level(self) -> ReproducibilityLevel:
        """Derived from the gaps only; it cannot be set independently."""
        return ReproducibilityLevel.PARTIAL if self.gaps else ReproducibilityLevel.COMPLETE

    @property
    def manifest_value(self) -> dict[str, Any]:
        value: dict[str, Any] = json.loads(self.canonical_manifest)
        return value

    @property
    def result_value(self) -> dict[str, Any]:
        value: dict[str, Any] = json.loads(self.canonical_result)
        return value

    @classmethod
    def from_canonical(cls, manifest_text: str, result_text: str) -> ReproducibilityBaseline:
        """Validate that both texts are canonical and derive every fingerprint and the gaps.

        Raises:
            ValueError: a text is not valid JSON, is not in canonical form (so a digest over the
                text would not be a digest of the canonical value), or lacks a required section.
        """
        manifest = _parse_canonical(manifest_text, "manifest")
        _parse_canonical(result_text, "result")
        if not isinstance(manifest, dict) or any(k not in manifest for k in _REQUIRED_SECTIONS):
            message = "a manifest must be an object with request, methodology and evidence"
            raise ValueError(message)
        try:
            gaps = gaps_from_evidence(manifest["evidence"])
        except (KeyError, TypeError) as exc:
            message = "the manifest evidence section lacks the structure its schema requires"
            raise ValueError(message) from exc
        return cls(
            canonical_manifest=manifest_text,
            canonical_result=result_text,
            fingerprints=ManifestFingerprints(
                request=fingerprint_of(manifest["request"]),
                methodology=fingerprint_of(manifest["methodology"]),
                evidence=fingerprint_of(manifest["evidence"]),
                manifest=fingerprint_of_text(manifest_text),
            ),
            result_fingerprint=fingerprint_of_text(result_text),
            gaps=gaps,
        )


def _parse_canonical(text: str, what: str) -> Any:
    try:
        parsed = json.loads(text)
        canonical = canonical_text(parsed)
    except (ValueError, TypeError, RecursionError) as exc:
        message = f"the {what} text is not valid canonical JSON ({type(exc).__name__})"
        raise ValueError(message) from exc
    if canonical != text:
        message = f"the {what} text is not in canonical form"
        raise ValueError(message)
    return parsed
