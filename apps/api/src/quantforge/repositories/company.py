"""Company (issuer) persistence.

Deliberately not a generic ``BaseRepository``. Every repository here exposes
only the queries the domain actually needs, which keeps the access patterns
visible and reviewable. A generic CRUD wrapper would hide the interesting part
-- the point-in-time and interval logic -- behind ``find_by(**kwargs)``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.orm import Session

from quantforge.models.company import Company
from quantforge.models.filing import SecFiling
from quantforge.models.financial_fact import FinancialFactSubmission
from quantforge.models.security import Listing, Security

__all__ = ["CompanyDirectoryPage", "CompanyDirectoryRow", "CompanyRepository"]


@dataclass(frozen=True, slots=True)
class CompanyDirectoryRow:
    """One company with a stored SEC CIK, its current tickers and its stored-filing counts."""

    cik: str
    name: str
    tickers: tuple[str, ...]
    stored_filings: int
    filings_with_facts: int


@dataclass(frozen=True, slots=True)
class CompanyDirectoryPage:
    rows: tuple[CompanyDirectoryRow, ...]
    total: int


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

    def get_by_cik(self, cik: str) -> Company | None:
        """The company stored under this exact ten-digit CIK (unique), or ``None``."""
        return self._session.scalar(sa.select(Company).where(Company.cik == cik))

    def current_tickers(self, company_id: uuid.UUID) -> tuple[str, ...]:
        """Tickers of the company's open (``valid_to IS NULL``) listings, sorted and distinct."""
        rows = self._session.scalars(
            sa.select(Listing.ticker)
            .join(Security, Security.id == Listing.security_id)
            .where(Security.company_id == company_id, Listing.valid_to.is_(None))
        )
        return tuple(sorted(set(rows)))

    def directory(self, *, query: str | None, limit: int, offset: int) -> CompanyDirectoryPage:
        """Companies that have a stored CIK, by display name, optionally narrowed by ``query``.

        ``query`` matches (OR): the exact CIK (digits are zero-padded to ten), a CURRENT ticker
        (case-insensitive, exact) or a case-insensitive substring of the legal or display name. It
        never reaches a provider. Counts are stored ``SecFiling`` rows and those with at least one
        FSDS submission; they say nothing about what exists at the SEC.
        """
        match: sa.ColumnElement[bool] = sa.true()
        if query:
            text = query.strip()
            escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            ticker_hit = (
                sa.select(Security.company_id)
                .join(Listing, Listing.security_id == Security.id)
                .where(Listing.valid_to.is_(None), Listing.ticker == text.upper())
            )
            conditions: list[sa.ColumnElement[bool]] = [
                Company.legal_name.ilike(f"%{escaped}%", escape="\\"),
                Company.display_name.ilike(f"%{escaped}%", escape="\\"),
                Company.id.in_(ticker_hit),
            ]
            if text.isdigit() and len(text) <= 10:  # noqa: PLR2004 - a CIK has ten digits
                conditions.append(Company.cik == text.zfill(10))
            match = sa.or_(*conditions)
        base = sa.select(Company).where(Company.cik.is_not(None), match)
        total = self._session.scalar(sa.select(sa.func.count()).select_from(base.subquery())) or 0
        companies = list(
            self._session.scalars(
                base.order_by(Company.display_name, Company.id).limit(limit).offset(offset)
            )
        )
        ids = [c.id for c in companies]
        tickers: dict[uuid.UUID, set[str]] = {i: set() for i in ids}
        stored: dict[uuid.UUID, int] = dict.fromkeys(ids, 0)
        with_facts: dict[uuid.UUID, int] = dict.fromkeys(ids, 0)
        if ids:
            for company_id, ticker in self._session.execute(
                sa.select(Security.company_id, Listing.ticker)
                .join(Listing, Listing.security_id == Security.id)
                .where(Security.company_id.in_(ids), Listing.valid_to.is_(None))
            ):
                tickers[company_id].add(ticker)
            for company_id, count in self._session.execute(
                sa.select(SecFiling.company_id, sa.func.count())
                .where(SecFiling.company_id.in_(ids))
                .group_by(SecFiling.company_id)
            ):
                stored[company_id] = count
            for company_id, count in self._session.execute(
                sa.select(SecFiling.company_id, sa.func.count(sa.distinct(SecFiling.id)))
                .join(
                    FinancialFactSubmission, FinancialFactSubmission.sec_filing_id == SecFiling.id
                )
                .where(SecFiling.company_id.in_(ids))
                .group_by(SecFiling.company_id)
            ):
                with_facts[company_id] = count
        return CompanyDirectoryPage(
            rows=tuple(
                CompanyDirectoryRow(
                    cik=c.cik or "",
                    name=c.display_name,
                    tickers=tuple(sorted(tickers[c.id])),
                    stored_filings=stored[c.id],
                    filings_with_facts=with_facts[c.id],
                )
                for c in companies
            ),
            total=total,
        )
