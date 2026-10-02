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

import sqlalchemy as sa
from sqlalchemy.orm import Session

from quantforge.models.security import Listing, Security

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


def _valid_listings_for_security(
    session: Session,
    *,
    security_id: uuid.UUID,
    on_date: date,
) -> list[Listing]:
    """Return every listing of one security valid on ``on_date``.

    Mirrors the half-open interval condition in
    :meth:`~quantforge.repositories.security.ListingRepository.resolve_by_ticker`:
    ``valid_from <= on_date`` and either ``valid_to IS NULL`` (still current)
    or ``valid_to > on_date`` (exclusive end). More than one row can
    legitimately come back -- a security can be dual-listed on two venues over
    the same period -- which is exactly the ambiguity the caller must resolve.
    """
    statement = sa.select(Listing).where(
        Listing.security_id == security_id,
        Listing.valid_from <= on_date,
        sa.or_(Listing.valid_to.is_(None), Listing.valid_to > on_date),
    )
    return list(session.scalars(statement))


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

    Resolution order, matching the existing primary-listing semantics in
    :class:`~quantforge.models.security.Listing` rather than inventing a new
    rule:

    1. For each candidate security, find the listings valid on ``on_date``.
    2. A security with exactly one valid listing contributes that listing.
    3. A security with several valid listings (dual-venue) contributes its
       ``is_primary`` one, if exactly one of the valid listings carries that
       flag.
    4. Otherwise -- no valid listing, or several with no single primary --
       that security contributes an ambiguity rather than a silent guess.

    If the company (or the single requested security) ends up with more than
    one candidate across all of its securities, the result is
    ``AMBIGUOUS_LISTING``: this schema has no column on ``EarningsEvent``
    saying which of a multi-security issuer's instruments the reported
    figures should be read against, so a caller facing a real multi-security
    issuer must pass ``security_id`` explicitly.

    Returns:
        A :class:`ListingResolution` describing the outcome. Never raises for
        an ordinary "no listing" or "ambiguous" case -- those are reported in
        the result, not as exceptions, so a caller can log and skip one event
        without aborting a batch.
    """
    # An explicit ``security_id`` only narrows the company's own securities: it
    # is never trusted on its own, so another company's security cannot be
    # paired with this company's event.
    ownership = sa.select(Security.id).where(Security.company_id == company_id)
    if security_id is not None:
        ownership = ownership.where(Security.id == security_id)
    security_ids = list(session.scalars(ownership))
    if security_id is not None and not security_ids:
        return ListingResolution(
            status=ListingResolutionStatus.NO_LISTING_FOR_DATE,
            listing_id=None,
            security_id=None,
            reason=f"security {security_id} does not belong to company {company_id}",
        )

    candidates: list[Listing] = []
    for sid in security_ids:
        valid = _valid_listings_for_security(session, security_id=sid, on_date=on_date)
        if not valid:
            continue
        if len(valid) == 1:
            candidates.append(valid[0])
            continue
        primaries = [listing for listing in valid if listing.is_primary]
        if len(primaries) == 1:
            candidates.append(primaries[0])
        else:
            # Several valid listings for this one security and no single
            # primary to prefer -- an ambiguity in its own right, carried
            # through so the final count below reports it as such rather
            # than silently resolving to the first row a query happened to
            # return.
            candidates.extend(valid)

    if not candidates:
        return ListingResolution(
            status=ListingResolutionStatus.NO_LISTING_FOR_DATE,
            listing_id=None,
            security_id=None,
            reason=f"no listing of company {company_id} was valid on {on_date}",
        )

    if len(candidates) > 1:
        return ListingResolution(
            status=ListingResolutionStatus.AMBIGUOUS_LISTING,
            listing_id=None,
            security_id=None,
            reason=(
                f"{len(candidates)} candidate listings were valid on {on_date}; "
                "pass an explicit security_id to disambiguate"
            ),
        )

    chosen = candidates[0]
    return ListingResolution(
        status=ListingResolutionStatus.RESOLVED,
        listing_id=chosen.id,
        security_id=chosen.security_id,
        reason="exactly one listing was valid on this date",
        exchange_mic=chosen.exchange.mic,
    )
