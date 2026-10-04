"""Insert-only persistence of immutable research records (Step 10.9B).

Deliberately exposes no update and no delete: a baseline is added, read, and never altered (a
database trigger enforces the same). Bound to the caller's session; nothing is committed here.
"""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from quantforge.models.research_record import ResearchObservationRecord

__all__ = ["ResearchRecordRepository"]


class ResearchRecordRepository:
    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    def insert_if_absent(self, values: dict[str, Any]) -> uuid.UUID | None:
        """INSERT the record unless an identical ``(manifest, result)`` pair already exists.

        Atomic ``ON CONFLICT DO NOTHING`` on the unique pair, so two concurrent saves of the same
        baseline cannot raise: exactly one inserts (its id is returned) and the other gets ``None``
        and re-reads the winner.
        """
        statement = (
            postgresql.insert(ResearchObservationRecord)
            .values(id=uuid.uuid4(), **values)
            .on_conflict_do_nothing(constraint="uq_research_observation_record_manifest_result")
            .returning(ResearchObservationRecord.id)
        )
        return self._session.execute(statement).scalar_one_or_none()

    def get(self, record_id: uuid.UUID) -> ResearchObservationRecord | None:
        return self._session.get(ResearchObservationRecord, record_id)

    def for_manifest(self, manifest_fingerprint: str) -> list[ResearchObservationRecord]:
        """Every record that shares a manifest fingerprint, oldest first."""
        return list(
            self._session.scalars(
                sa.select(ResearchObservationRecord)
                .where(ResearchObservationRecord.manifest_fingerprint == manifest_fingerprint)
                .order_by(ResearchObservationRecord.created_at, ResearchObservationRecord.id)
            )
        )

    def for_pair(
        self, manifest_fingerprint: str, result_fingerprint: str
    ) -> ResearchObservationRecord | None:
        return self._session.scalar(
            sa.select(ResearchObservationRecord).where(
                ResearchObservationRecord.manifest_fingerprint == manifest_fingerprint,
                ResearchObservationRecord.result_fingerprint == result_fingerprint,
            )
        )
