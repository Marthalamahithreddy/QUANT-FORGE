"""Read-only lookup of stored SEC filing references (identity, form, items; never times)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.orm import Session

from quantforge.models.enums import SecFormType
from quantforge.models.filing import SecFiling

__all__ = ["StoredFilingRef", "StoredFilingRepository"]


@dataclass(frozen=True, slots=True)
class StoredFilingRef:
    """One stored filing row of an accession: who filed it and what it says it is.

    Deliberately carries no acceptance, filing-date or ingestion time: this exists to resolve
    identity and the stored form/items, not to say when anything was known.
    """

    sec_filing_id: uuid.UUID
    company_id: uuid.UUID
    accession_number: str
    form: SecFormType
    items: str | None
    source_id: uuid.UUID
    ingestion_run_id: uuid.UUID | None


class StoredFilingRepository:
    """SELECT-only access to ``sec_filing`` bound to the caller's session."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def for_accession(self, accession_number: str) -> list[StoredFilingRef]:
        """Every stored row of the exact accession (a joint filing has one per registrant)."""
        rows = self._session.execute(
            sa.select(
                SecFiling.id,
                SecFiling.company_id,
                SecFiling.accession_number,
                SecFiling.form,
                SecFiling.items,
                SecFiling.source_id,
                SecFiling.ingestion_run_id,
            )
            .where(SecFiling.accession_number == accession_number)
            .order_by(SecFiling.id)
        ).all()
        return [
            StoredFilingRef(
                sec_filing_id=row.id,
                company_id=row.company_id,
                accession_number=row.accession_number,
                form=row.form,
                items=row.items,
                source_id=row.source_id,
                ingestion_run_id=row.ingestion_run_id,
            )
            for row in rows
        ]
