"""Company (issuer) persistence.

Deliberately not a generic ``BaseRepository``. Every repository here exposes
only the queries the domain actually needs, which keeps the access patterns
visible and reviewable. A generic CRUD wrapper would hide the interesting part
-- the point-in-time and interval logic -- behind ``find_by(**kwargs)``.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Session

from quantforge.models.company import Company

__all__ = ["CompanyRepository"]


class CompanyRepository:
    """Reads and writes :class:`~quantforge.models.company.Company` rows.

    Bound to a :class:`~sqlalchemy.orm.Session` rather than creating its own, so
    it composes with the caller's transaction and never silently commits.
    """

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, company: Company) -> Company:
        """Stage a new issuer for insertion and return it.

        The caller owns the flush/commit. Returning the same instance keeps the
        pattern composable: create several related objects, then flush once.
        """
        self._session.add(company)
        return company

    def get(self, company_id: uuid.UUID) -> Company | None:
        """Return the issuer with this id, or ``None``.

        Not ``get_or_404``-style: absence is a normal outcome when resolving a
        reference id during ingestion, and raising here would force every caller
        to wrap the call.
        """
        return self._session.get(Company, company_id)

    def get_by_legal_name(self, legal_name: str) -> Company | None:
        """Return the issuer with this exact registered legal name, or ``None``.

        ``legal_name`` is deliberately **not** unique -- unrelated issuers share
        names across jurisdictions -- so this can legitimately return more than
        one row's worth of ambiguity. Callers that need certainty must reconcile
        by country code or by an external identifier. The method exists for
        review, not as an identity lookup.
        """
        return self._session.scalar(
            sa.select(Company).where(Company.legal_name == legal_name).limit(1)
        )

    def search_by_display_name(self, prefix: str, *, limit: int = 50) -> list[Company]:
        """Return issuers whose display name starts with ``prefix``.

        A prefix match uses the btree index on ``display_name``; substring and
        fuzzy search would need ``pg_trgm`` and are out of scope here.

        Args:
            prefix: Case-sensitive literal prefix of the display name.
            limit: Maximum rows to return. Bounded so a one-character prefix
                cannot pull an entire table into memory.
        """
        statement = (
            sa.select(Company)
            .where(Company.display_name.startswith(prefix))
            .order_by(Company.display_name)
            .limit(limit)
        )
        return list(self._session.scalars(statement))

    def list_all(self, *, limit: int = 100, offset: int = 0) -> list[Company]:
        """Return a page of issuers ordered by display name.

        Ordered by a stable, human-meaningful key so pagination cannot repeat or
        skip rows when two issuers share a display name.
        """
        statement = (
            sa.select(Company)
            .order_by(Company.display_name, Company.id)
            .limit(limit)
            .offset(offset)
        )
        return list(self._session.scalars(statement))
