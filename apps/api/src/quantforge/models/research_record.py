"""Immutable persisted research records (Step 10.9B).

One row is the historical BASELINE of one completed research computation: the exact canonical
manifest the computation claimed to consume, the exact canonical result it produced, their SHA-256
fingerprints, and the derived reproducibility level and gaps at the time. A later rerun is compared
against it; it never replaces it.

Identity (three different things, deliberately not collapsed)
-------------------------------------------------------------
* ``id`` -- the identity of THIS persisted record (its own UUID primary key);
* ``manifest_fingerprint`` -- identity of the computation's request + methodology + evidence;
* ``result_fingerprint`` -- identity of the produced observation.
``UNIQUE (manifest_fingerprint, result_fingerprint)`` makes an exact repeat idempotent (the service
returns the existing record) and also serves lookups by manifest. The same manifest with a DIFFERENT
result is not collapsed: the service refuses it, because it signals a methodology-version error,
unpinned evidence, data mutation or a bug.

Canonical content is stored as TEXT, not JSONB: JSONB re-orders keys and re-spaces numbers, so it
cannot preserve the exact bytes the digest was taken over. The fingerprint is verifiable against the
stored text (``sha256(text)``), and the loader re-checks that on every read.

Immutability: there is no ``updated_at``, the repository exposes no update or delete, and a database
trigger (migration 0013) rejects any UPDATE, DELETE or TRUNCATE. ``created_at`` is persistence metadata and is
never part of either fingerprint.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from quantforge.db.base import Base
from quantforge.models.base import CreatedAtMixin, UUIDPrimaryKeyMixin
from quantforge.models.enums import (
    REPRODUCIBILITY_LEVEL_LENGTH,
    ReproducibilityLevel,
    enum_check,
    enum_type,
)

__all__ = ["HASH_ALGORITHM_LENGTH", "SHA256_HEX_LENGTH", "ResearchObservationRecord"]

SHA256_HEX_LENGTH = 64
HASH_ALGORITHM_LENGTH = 16


class ResearchObservationRecord(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One immutable research baseline. See the module docstring."""

    __tablename__ = "research_observation_record"
    __table_args__ = (
        sa.UniqueConstraint(
            "manifest_fingerprint",
            "result_fingerprint",
            name="uq_research_observation_record_manifest_result",
        ),
        enum_check("reproducibility_level", ReproducibilityLevel),
        sa.CheckConstraint(
            "manifest_fingerprint ~ '^[0-9a-f]{64}$'", name="manifest_fingerprint_format"
        ),
        sa.CheckConstraint(
            "result_fingerprint ~ '^[0-9a-f]{64}$'", name="result_fingerprint_format"
        ),
        sa.CheckConstraint(
            "manifest_hash_algorithm = 'sha256'", name="manifest_hash_algorithm_known"
        ),
        sa.CheckConstraint("result_hash_algorithm = 'sha256'", name="result_hash_algorithm_known"),
        sa.CheckConstraint("length(canonical_manifest) > 0", name="canonical_manifest_nonempty"),
        sa.CheckConstraint("length(canonical_result) > 0", name="canonical_result_nonempty"),
        sa.CheckConstraint("jsonb_typeof(reproducibility_gaps) = 'array'", name="gaps_is_array"),
        sa.CheckConstraint(
            "(reproducibility_level = 'COMPLETE') = (jsonb_array_length(reproducibility_gaps) = 0)",
            name="level_matches_gaps",
        ),
    )

    manifest_fingerprint: Mapped[str] = mapped_column(
        sa.String(SHA256_HEX_LENGTH),
        nullable=False,
        doc="Digest of the canonical manifest (request + methodology + evidence).",
    )
    manifest_hash_algorithm: Mapped[str] = mapped_column(
        sa.String(HASH_ALGORITHM_LENGTH), nullable=False, doc="Algorithm of the manifest digest."
    )
    canonical_manifest: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        doc="The exact canonical manifest text the manifest digest was taken over.",
    )
    result_fingerprint: Mapped[str] = mapped_column(
        sa.String(SHA256_HEX_LENGTH),
        nullable=False,
        doc="Digest of the canonical result (the observation, roadmap known_gaps excluded).",
    )
    result_hash_algorithm: Mapped[str] = mapped_column(
        sa.String(HASH_ALGORITHM_LENGTH), nullable=False, doc="Algorithm of the result digest."
    )
    canonical_result: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        doc="The exact canonical result text, kept so a later difference can be explained by path.",
    )
    reproducibility_level: Mapped[ReproducibilityLevel] = mapped_column(
        enum_type(
            ReproducibilityLevel,
            name="reproducibility_level",
            length=REPRODUCIBILITY_LEVEL_LENGTH,
        ),
        nullable=False,
        doc="Derived from the gaps at save time; the loader re-derives and verifies it.",
    )
    reproducibility_gaps: Mapped[list[Any]] = mapped_column(
        postgresql.JSONB(astext_type=sa.Text()),
        nullable=False,
        doc="Sorted gap names (empty for COMPLETE), derived from the manifest's evidence.",
    )

    def __repr__(self) -> str:
        return (
            f"ResearchObservationRecord(id={self.id!r}, "
            f"manifest={self.manifest_fingerprint[:12]!r}, result={self.result_fingerprint[:12]!r})"
        )
