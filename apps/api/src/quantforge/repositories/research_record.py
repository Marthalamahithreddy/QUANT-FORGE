"""Insert-only persistence of immutable research records (Step 10.9B).

Deliberately exposes no update and no delete: a baseline is added, read, and never altered (a
database trigger enforces the same). Bound to the caller's session; nothing is committed here.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from quantforge.models.enums import ReproducibilityLevel
from quantforge.models.research_record import ResearchObservationRecord

__all__ = ["RecordListPage", "RecordListRow", "ResearchRecordRepository"]


@dataclass(frozen=True, slots=True)
class RecordListRow:
    """One stored record WITHOUT its canonical texts: columns plus the manifest's request object."""

    record_id: uuid.UUID
    created_at: datetime
    manifest_fingerprint: str
    manifest_hash_algorithm: str
    result_fingerprint: str
    level: ReproducibilityLevel
    gaps: list[str]
    #: ``canonical_manifest -> 'request'``, extracted by the database so the (large) canonical
    #: texts never cross the wire. Not integrity-validated here (see ``list_page``).
    request: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RecordListPage:
    rows: list[RecordListRow]
    total: int


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

    def list_page(self, *, limit: int, offset: int) -> RecordListPage:
        """A bounded page, newest first (``created_at`` DESC, then ``id`` DESC: a total order).

        Two statements, no per-row query: the page (stored columns plus the manifest's ``request``
        object, cast and extracted inside the database) and the total. The canonical texts are
        never selected. Rows here are summaries of stored columns and are NOT re-hashed: integrity
        is validated by :func:`~quantforge.research_records.load_research_record` on get/verify.
        The database CHECKs pin both algorithms to ``sha256``, so one is reported.

        The page and the total are two reads in the caller's transaction (READ COMMITTED): a record
        saved between them can make the total, and offset pages, disagree by that record.
        """
        record = ResearchObservationRecord
        # Page the ids first, over the narrow sort columns only. The JSONB cast of a ~5 KB text is
        # then evaluated for the page's rows alone: in one flat SELECT PostgreSQL casts EVERY row
        # before sorting (measured: 45 ms vs 0.75 ms at 5,000 records), so cost would follow the
        # table, not the page.
        page = (
            sa.select(record.id)
            .order_by(record.created_at.desc(), record.id.desc())
            .limit(limit)
            .offset(offset)
            .subquery("page")
        )
        request = sa.cast(record.canonical_manifest, postgresql.JSONB)["request"].label("request")
        rows = self._session.execute(
            sa.select(
                record.id,
                record.created_at,
                record.manifest_fingerprint,
                record.manifest_hash_algorithm,
                record.result_fingerprint,
                record.reproducibility_level,
                record.reproducibility_gaps,
                request,
            )
            .join(page, page.c.id == record.id)
            .order_by(record.created_at.desc(), record.id.desc())
        ).all()
        total = self._session.execute(sa.select(sa.func.count()).select_from(record)).scalar_one()
        return RecordListPage(
            rows=[
                RecordListRow(
                    record_id=row.id,
                    created_at=row.created_at,
                    manifest_fingerprint=row.manifest_fingerprint,
                    manifest_hash_algorithm=row.manifest_hash_algorithm,
                    result_fingerprint=row.result_fingerprint,
                    level=row.reproducibility_level,
                    gaps=list(row.reproducibility_gaps),
                    request=row.request,
                )
                for row in rows
            ],
            total=total,
        )

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
