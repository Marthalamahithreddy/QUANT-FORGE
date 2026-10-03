"""Resolve which listing an earnings event's issuer was trading under.

An :class:`~quantforge.models.earnings.EarningsEvent` is attached to a
``company_id``, not to a ``listing_id`` or even a ``security_id`` -- reported
figures are an issuer-level fact, and QuantForge does not assume an issuer has
exactly one instrument. Any research that wants to align the event against
price data must therefore resolve, as of ``event_date``, which listing the
figures should be read against. That resolution is a query, not a
calculation, which is why it lives in its own module rather than in
:mod:`quantforge.research.calendar`: this is the one piece of Step 8.1/8.2
that touches the database.

Using *today's* ticker or listing for a historical event is exactly the
mistake this module exists to prevent -- a company can rename its ticker, add
a second share class or move venues, and an event from years ago must resolve
against the listing that was actually valid then, per the half-open
``[valid_from, valid_to)`` interval :class:`~quantforge.models.security.Listing`
already enforces (see :meth:`ListingRepository.resolve_by_ticker
<quantforge.repositories.security.ListingRepository.resolve_by_ticker>` for
the ticker-keyed sibling of this date logic).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from sqlalchemy.orm import Session

from quantforge.identity.listing_history import Evidence, ResolutionStatus
from quantforge.identity.resolver import HistoricalListingResolver

__all__ = ["ListingResolution", "ListingResolutionStatus", "resolve_company_listing_for_date"]


class ListingResolutionStatus(StrEnum):
    """The outcome of resolving a company to one listing as of a date."""

    #: Exactly one listing was valid; ``listing_id``/``security_id`` are set.
    RESOLVED = "RESOLVED"

    #: No listing of any of the company's securities was valid on that date.
    NO_LISTING_FOR_DATE = "NO_LISTING_FOR_DATE"

    #: More than one listing was valid and candidate-listing semantics (the
    #: single current-primary rule) could not narrow it to one. Resolved by
    #: the caller supplying an explicit ``security_id``, never by silently
    #: picking one.
    AMBIGUOUS_LISTING = "AMBIGUOUS_LISTING"

    #: Sourced listing history contradicts itself (or a first-observed listing) on this
    #: date. Never resolved by picking a side.
    CONFLICTING_HISTORY = "CONFLICTING_HISTORY"


@dataclass(frozen=True, slots=True)
class ListingResolution:
    """The result of resolving one company to a listing as of a date."""

    status: ListingResolutionStatus
    listing_id: uuid.UUID | None
    security_id: uuid.UUID | None
    reason: str
    #: MIC of the *historically resolved* listing's exchange (set only when
    #: RESOLVED). Read from the same listing row the resolution chose, so it is
    #: the venue the security traded on at ``on_date`` -- never a current
    #: listing, a ticker lookup, or a company-level default.
    exchange_mic: str | None = None
    #: How the resolution is proven: a first-observed listing, or source-stated history.
    #: ``None`` unless RESOLVED.
    evidence: Evidence | None = None


def resolve_company_listing_for_date(
    session: Session,
    *,
    company_id: uuid.UUID,
    on_date: date,
    security_id: uuid.UUID | None = None,
) -> ListingResolution:
    """Resolve the listing an earnings event's issuer traded under on a date.

    Args:
        session: Database session to read from.
        company_id: The issuer the earnings event belongs to.
        on_date: The date to resolve validity for -- the event's
            ``event_date``, not today.
        security_id: Restrict resolution to one of the company's securities,
            for a caller that already knows which instrument it means (e.g. a
            dual-class issuer where the study is explicitly about the common
            shares). It must belong to ``company_id``; a security of any other
            company (or a nonexistent one) resolves to ``NO_LISTING_FOR_DATE``
            with an ownership reason rather than being used. When omitted,
            every security the company has issued is a candidate.

    Resolution is delegated to :class:`~quantforge.identity.resolver.HistoricalListingResolver`,
    the one resolver every historical consumer shares:

    1. Evidence is a first-observed ``Listing`` interval (``valid_from`` is when QuantForge first
       saw the listing, never the listing's start) and/or source-stated history
       (``ListingHistoryFact``). Sourced history can prove a date earlier than ``valid_from``; it
       can never turn a gap, an ambiguity or a conflict into a resolution.
    2. With no sourced history, behaviour is exactly the long-standing one: the listings valid on
       ``on_date`` per ``[valid_from, valid_to)``, a single listing or the single ``is_primary`` one.
    3. A security with several candidate listings and no single primary, several securities for the
       company, or several tickers is ``AMBIGUOUS_LISTING``; contradictory history is
       ``CONFLICTING_HISTORY``; no proven listing (including a proven ticker with no stored
       listing row, e.g. a retired symbol never ingested) is ``NO_LISTING_FOR_DATE``.

    Returns:
        A :class:`ListingResolution` describing the outcome. Never raises for
        an ordinary "no listing" or "ambiguous" case -- those are reported in
        the result, not as exceptions, so a caller can log and skip one event
        without aborting a batch.
    """
    # One resolver for every historical consumer (alignment, earnings ingestion, the earnings API,
    # split ingestion). Sourced history can prove a date earlier than a listing's first-observed
    # ``valid_from``; it can never turn a gap, an ambiguity or a conflict into a resolution.
    outcome = HistoricalListingResolver(session).resolve_company_on(
        company_id, on_date, security_id=security_id
    )
    if outcome.status is ResolutionStatus.CONFLICT:
        return ListingResolution(
            status=ListingResolutionStatus.CONFLICTING_HISTORY,
            listing_id=None,
            security_id=None,
            reason=outcome.reason,
        )
    if outcome.status is ResolutionStatus.AMBIGUOUS:
        return ListingResolution(
            status=ListingResolutionStatus.AMBIGUOUS_LISTING,
            listing_id=None,
            security_id=None,
            reason=outcome.reason,
        )
    if outcome.status is ResolutionStatus.RESOLVED and outcome.listing_id is not None:
        return ListingResolution(
            status=ListingResolutionStatus.RESOLVED,
            listing_id=outcome.listing_id,
            security_id=outcome.security_id,
            reason=outcome.reason,
            exchange_mic=outcome.exchange_mic,
            evidence=outcome.evidence,
        )
    return ListingResolution(
        status=ListingResolutionStatus.NO_LISTING_FOR_DATE,
        listing_id=None,
        security_id=None,
        reason=outcome.reason,
    )
