"""Security, exchange, listing and identifier persistence.

The centrepiece is :meth:`ListingRepository.resolve_by_ticker`, which is the
lookup every ingestion and research path starts from: given a ticker, a venue
and a date, return the listing that was trading then. Getting its half-open
interval semantics right is what makes a point-in-time join correct.
"""

from __future__ import annotations

import uuid
from datetime import date

import sqlalchemy as sa
from sqlalchemy.orm import Session

from quantforge.models.enums import IdentifierType
from quantforge.models.security import Exchange, Listing, Security, SecurityIdentifier

__all__ = [
    "ExchangeRepository",
    "ListingRepository",
    "SecurityIdentifierRepository",
    "SecurityRepository",
]


class ExchangeRepository:
    """Reads and writes venue rows, keyed by MIC."""

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, exchange: Exchange) -> Exchange:
        """Stage a new venue for insertion and return it."""
        self._session.add(exchange)
        return exchange

    def get(self, exchange_id: uuid.UUID) -> Exchange | None:
        """Return the venue with this id, or ``None``."""
        return self._session.get(Exchange, exchange_id)

    def get_by_mic(self, mic: str) -> Exchange | None:
        """Return the venue with this MIC, or ``None``.

        ``mic`` is upper-cased before lookup to match the canonical form the
        database ``CHECK`` enforces, so ``"xnys"`` resolves to ``"XNYS"``.
        """
        return self._session.scalar(sa.select(Exchange).where(Exchange.mic == mic.upper()))


class SecurityRepository:
    """Reads and writes instrument rows."""

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, security: Security) -> Security:
        """Stage a new instrument for insertion and return it."""
        self._session.add(security)
        return security

    def get(self, security_id: uuid.UUID) -> Security | None:
        """Return the instrument with this id, or ``None``."""
        return self._session.get(Security, security_id)

    def list_for_company(self, company_id: uuid.UUID) -> list[Security]:
        """Return every instrument issued by a company, oldest first."""
        statement = (
            sa.select(Security)
            .where(Security.company_id == company_id)
            .order_by(Security.created_at)
        )
        return list(self._session.scalars(statement))


class ListingRepository:
    """Reads and writes venue listings, including point-in-time ticker resolution."""

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, listing: Listing) -> Listing:
        """Stage a new listing for insertion and return it."""
        self._session.add(listing)
        return listing

    def get(self, listing_id: uuid.UUID) -> Listing | None:
        """Return the listing with this id, or ``None``."""
        return self._session.get(Listing, listing_id)

    def resolve_by_ticker(
        self,
        ticker: str,
        *,
        on_date: date,
        exchange_id: uuid.UUID | None = None,
    ) -> Listing | None:
        """Return the listing that traded under ``ticker`` on ``on_date``.

        Implements the half-open validity rule ``[valid_from, valid_to)``:
        a listing matches when ``valid_from <= on_date`` and either ``valid_to``
        is NULL (still current) or ``valid_to > on_date`` (exclusive end). The
        exclusive upper bound is what lets a ticker handover share a boundary
        date with no gap and no double match.

        ``ticker`` is upper-cased to match the canonical stored form.

        Args:
            ticker: Venue symbol, in any case.
            on_date: The session date to resolve for. A ``DATE``, matching the
                column type, so no time or zone conversion can shift the answer.
            exchange_id: Restrict to one venue. Optional because a ticker can be
                genuinely ambiguous across venues, and silently picking one would
                attach prices to the wrong security.

        Returns:
            The matching listing, or ``None`` if the ticker was not listed on
            that date. ``None`` is a meaningful answer -- it distinguishes "this
            ticker did not exist yet" from "we have no data for it".
        """
        conditions = [
            Listing.ticker == ticker.upper(),
            Listing.valid_from <= on_date,
            sa.or_(Listing.valid_to.is_(None), Listing.valid_to > on_date),
        ]
        if exchange_id is not None:
            conditions.append(Listing.exchange_id == exchange_id)
        return self._session.scalar(sa.select(Listing).where(*conditions))

    def ticker_history(
        self,
        *,
        exchange_id: uuid.UUID,
        ticker: str,
    ) -> list[Listing]:
        """Return every listing period ever recorded for a (venue, ticker).

        Ordered by ``valid_from`` so consecutive periods reconstruct the
        handover history. A ticker reused by a different issuer years later
        shows up here as an earlier period belonging to a different
        ``security_id`` -- which is precisely the case a global
        ``UNIQUE(ticker)`` constraint would have made unrepresentable.
        """
        statement = (
            sa.select(Listing)
            .where(Listing.exchange_id == exchange_id, Listing.ticker == ticker.upper())
            .order_by(Listing.valid_from)
        )
        return list(self._session.scalars(statement))

    def list_for_security(self, security_id: uuid.UUID) -> list[Listing]:
        """Return every listing of one instrument, earliest validity first."""
        statement = (
            sa.select(Listing)
            .where(Listing.security_id == security_id)
            .order_by(Listing.valid_from)
        )
        return list(self._session.scalars(statement))

    def current_primary_listing(self, security_id: uuid.UUID) -> Listing | None:
        """Return the instrument's still-open primary listing, or ``None``.

        Relies on the partial unique index that permits at most one such row, so
        the result is unambiguous by construction rather than by a ``LIMIT 1``.
        """
        statement = sa.select(Listing).where(
            Listing.security_id == security_id,
            Listing.is_primary.is_(True),
            Listing.valid_to.is_(None),
        )
        return self._session.scalar(statement)


class SecurityIdentifierRepository:
    """Reads and writes external identifier codes."""

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, identifier: SecurityIdentifier) -> SecurityIdentifier:
        """Stage a new identifier for insertion and return it."""
        self._session.add(identifier)
        return identifier

    def get_by_code(self, identifier_type: IdentifierType, value: str) -> SecurityIdentifier | None:
        """Return the identifier with this scheme and value, or ``None``.

        This is the natural ingestion join key: an inbound record usually
        arrives labelled by ISIN or CUSIP rather than by an internal id. The
        ``(identifier_type, identifier_value)`` unique constraint means the
        answer is at most one row.
        """
        return self._session.scalar(
            sa.select(SecurityIdentifier).where(
                SecurityIdentifier.identifier_type == identifier_type,
                SecurityIdentifier.identifier_value == value,
            )
        )

    def list_for_security(self, security_id: uuid.UUID) -> list[SecurityIdentifier]:
        """Return every external code recorded for an instrument."""
        statement = sa.select(SecurityIdentifier).where(
            SecurityIdentifier.security_id == security_id
        )
        return list(self._session.scalars(statement))
