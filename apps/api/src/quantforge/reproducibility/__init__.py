"""Reproducibility foundation (Step 10.9A): manifest, fingerprints, level, re-run verifier.

Pure/read-only: no persistence, no migration, no provider. Sits above ``observation``.
"""

from __future__ import annotations

from quantforge.reproducibility.canonical import Fingerprint, canonical_bytes, fingerprint_of
from quantforge.reproducibility.manifest import (
    ReproducibilityGap,
    ReproducibilityLevel,
    ReproducibilityRecord,
    build_reproducibility_record,
)
from quantforge.reproducibility.verifier import (
    VerificationReport,
    VerificationStatus,
    compare_records,
    verify_rerun,
)

__all__ = [
    "Fingerprint",
    "ReproducibilityGap",
    "ReproducibilityLevel",
    "ReproducibilityRecord",
    "VerificationReport",
    "VerificationStatus",
    "build_reproducibility_record",
    "canonical_bytes",
    "compare_records",
    "fingerprint_of",
    "verify_rerun",
]
