"""Read-only earnings-event endpoints.

These routes expose what is already stored; they derive nothing the research
layer does not already define. Point-in-time eligibility and EPS surprise come
from :mod:`quantforge.research`, so a client never has to (and must not)
re-implement either. Monetary and EPS figures are serialised as decimal
strings, never JSON numbers, so no consumer is tempted into float arithmetic.

Each row is one stored OBSERVATION, not a de-duplicated logical event. A
revision, a preliminary versus final release, or a second vendor's figure for
the same fiscal period are separate rows (see
:class:`~quantforge.models.earnings.EarningsEvent`), and all are returned.
No "latest" selection is made here: choosing a revision requires an explicit
as-of time, which belongs to the research layer, not to this read surface.

Filters: ``ticker`` is a case-insensitive EXACT match against a listing that
was valid on the event's own date (half-open ``[valid_from, valid_to)``), never
against the company's current ticker. ``date_from`` and ``date_to`` are both
inclusive.

Deliberately not exposed: the ingestion run id, the row fingerprint and any raw
payload location.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, PlainSerializer
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.orm import Session

from quantforge.db.session import get_session
from quantforge.identity.listing_history import SecurityHistory
from quantforge.identity.resolver import HistoricalListingResolver
from quantforge.models.earnings import EarningsEvent
from quantforge.models.enums import AvailabilityBasis, EarningsTiming, ProviderType, SecurityType
from quantforge.models.security import Listing, Security
from quantforge.research.availability import (
    PointInTimeEligibility,
    ResearchMode,
    classify_point_in_time_eligibility,
)
from quantforge.research.eps_surprise import EpsSurpriseStatus, compute_eps_surprise

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/earnings-events", tags=["earnings"])

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
MAX_OFFSET = 1_000_000

_ELIGIBILITY_REASONS: dict[PointInTimeEligibility, str] = {
    PointInTimeEligibility.ELIGIBLE: (
        "Approved availability evidence supports a point-in-time claim."
    ),
    PointInTimeEligibility.INELIGIBLE_NO_AVAILABILITY_EVIDENCE: (
        "The source did not supply a historical availability time (observed_at is not set)."
    ),
    PointInTimeEligibility.INELIGIBLE_UNAPPROVED_AVAILABILITY_BASIS: (
        "An availability time exists, but its basis is missing or not approved for "
        "point-in-time use."
    ),
}


def _fixed_point(value: Decimal) -> str:
    # ``str(Decimal)`` may use exponent notation ("1E-7"); clients get plain
    # fixed-point text so no consumer needs to parse an exponent.
    return format(value, "f")


#: A Decimal that always serialises to fixed-point text, never a JSON number.
DecimalText = Annotated[Decimal, PlainSerializer(_fixed_point, return_type=str, when_used="json")]


class CompanyRef(BaseModel):
    id: uuid.UUID
    display_name: str
    legal_name: str
    cik: str | None


class SourceRef(BaseModel):
    name: str
    provider_type: ProviderType


class ListingRef(BaseModel):
    """One of the company's venue listings. Not necessarily the one reporting."""

    ticker: str
    mic: str
    exchange_name: str
    security_name: str
    security_type: SecurityType
    valid_from: date
    valid_to: date | None = Field(description="Exclusive end of validity; null while current.")
    is_primary: bool
    valid_on_event_date: bool = Field(
        description="Whether the listing was valid on the event date, half-open [from, to)."
    )


class Availability(BaseModel):
    observed_at: datetime | None
    observed_at_basis: AvailabilityBasis | None
    pit_eligibility: PointInTimeEligibility
    pit_reason: str


class EpsSurprise(BaseModel):
    status: EpsSurpriseStatus
    absolute: DecimalText | None
    ratio: DecimalText | None
    reason: str


class EarningsEventItem(BaseModel):
    id: uuid.UUID
    company: CompanyRef
    source: SourceRef
    listings: list[ListingRef]
    resolved_tickers: list[str] = Field(
        description=(
            "Tickers proven for this company on the event date by the historical resolver "
            "(first-observed listings and source-stated history). May name a ticker no stored "
            "listing carries; empty when nothing is proven or the evidence conflicts."
        )
    )
    fiscal_year: int
    fiscal_quarter: int | None
    event_date: date
    event_timestamp: datetime | None
    timing: EarningsTiming
    currency: str | None
    reported_eps: DecimalText | None
    estimated_eps: DecimalText | None
    reported_revenue: DecimalText | None
    estimated_revenue: DecimalText | None
    eps_surprise: EpsSurprise
    availability: Availability
    ingested_at: datetime


class EarningsEventPage(BaseModel):
    items: list[EarningsEventItem]
    total: int
    limit: int
    offset: int


class ListParams(BaseModel):
    """Query parameters for the list endpoint."""

    model_config = {"extra": "forbid", "str_strip_whitespace": True}

    limit: int = Field(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT)
    offset: int = Field(0, ge=0, le=MAX_OFFSET)
    ticker: str | None = Field(None, min_length=1, max_length=32, pattern=r"^\S(.*\S)?$")
    date_from: date | None = None
    date_to: date | None = None


SessionDep = Annotated[Session, Depends(get_session)]


def _listings_by_company(
    session: Session, company_ids: set[uuid.UUID]
) -> dict[uuid.UUID, list[Listing]]:
    if not company_ids:
        return {}
    rows = session.scalars(
        sa.select(Listing)
        .join(Security, Listing.security_id == Security.id)
        .where(Security.company_id.in_(company_ids))
        .order_by(Listing.is_primary.desc(), Listing.valid_from.desc(), Listing.ticker)
    ).all()
    grouped: dict[uuid.UUID, list[Listing]] = {}
    for listing in rows:
        grouped.setdefault(listing.security.company_id, []).append(listing)
    return grouped


class _Histories:
    """Derived listing histories for the securities of a set of companies (two queries)."""

    __slots__ = ("_by_company", "_resolver")

    def __init__(self, session: Session, company_ids: set[uuid.UUID]) -> None:
        self._resolver = HistoricalListingResolver(session)
        securities: dict[uuid.UUID, list[uuid.UUID]] = {}
        if company_ids:
            for sid, cid in session.execute(
                sa.select(Security.id, Security.company_id).where(
                    Security.company_id.in_(company_ids)
                )
            ):
                securities.setdefault(cid, []).append(sid)
        every = self._resolver.histories(sid for sids in securities.values() for sid in sids)
        self._by_company: dict[uuid.UUID, list[SecurityHistory]] = {
            cid: [every[sid] for sid in sids] for cid, sids in securities.items()
        }

    def valid_listing_ids(self, company_id: uuid.UUID, on: date) -> frozenset[uuid.UUID]:
        """Listings whose ticker is proven on ``on`` -- the same rule the resolver applies."""
        ids: set[uuid.UUID] = set()
        for history in self._by_company.get(company_id, []):
            ids |= self._resolver.valid_listing_ids_on(history, on)
        return frozenset(ids)

    def proven_tickers(self, company_id: uuid.UUID, on: date) -> list[str]:
        tickers: set[str] = set()
        for history in self._by_company.get(company_id, []):
            if history.conflict_on(on) is None:
                tickers |= history.sourced_tickers_on(on) | history.provisional_tickers_on(on)
        return sorted(tickers)


def _to_item(
    event: EarningsEvent, listings: list[Listing], histories: _Histories
) -> EarningsEventItem:
    eligibility = classify_point_in_time_eligibility(
        ResearchMode.POINT_IN_TIME,
        observed_at=event.observed_at,
        observed_at_basis=event.observed_at_basis,
    )
    surprise = compute_eps_surprise(
        reported_eps=event.reported_eps, estimated_eps=event.estimated_eps
    )
    company = event.company
    valid_ids = histories.valid_listing_ids(event.company_id, event.event_date)
    return EarningsEventItem(
        id=event.id,
        company=CompanyRef(
            id=company.id,
            display_name=company.display_name,
            legal_name=company.legal_name,
            cik=company.cik,
        ),
        source=SourceRef(name=event.source.name, provider_type=event.source.provider_type),
        resolved_tickers=histories.proven_tickers(event.company_id, event.event_date),
        listings=[
            ListingRef(
                ticker=listing.ticker,
                mic=listing.exchange.mic,
                exchange_name=listing.exchange.name,
                security_name=listing.security.name,
                security_type=listing.security.security_type,
                valid_from=listing.valid_from,
                valid_to=listing.valid_to,
                is_primary=listing.is_primary,
                valid_on_event_date=listing.id in valid_ids,
            )
            for listing in listings
        ],
        fiscal_year=event.fiscal_year,
        fiscal_quarter=event.fiscal_quarter,
        event_date=event.event_date,
        event_timestamp=event.event_timestamp,
        timing=event.timing,
        currency=event.currency,
        reported_eps=event.reported_eps,
        estimated_eps=event.estimated_eps,
        reported_revenue=event.reported_revenue,
        estimated_revenue=event.estimated_revenue,
        eps_surprise=EpsSurprise(
            status=surprise.status,
            absolute=surprise.absolute_surprise,
            ratio=surprise.surprise_ratio,
            reason=surprise.reason,
        ),
        availability=Availability(
            observed_at=event.observed_at,
            observed_at_basis=event.observed_at_basis,
            pit_eligibility=eligibility,
            pit_reason=_ELIGIBILITY_REASONS[eligibility],
        ),
        ingested_at=event.ingested_at,
    )


def _unavailable(exc: OperationalError | InterfaceError) -> HTTPException:
    # Class name only: driver messages can embed the DSN.
    logger.warning("Earnings read failed: %s", type(exc).__name__, exc_info=True)
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database unavailable."
    )


@router.get(
    "",
    response_model=EarningsEventPage,
    summary="List earnings events",
    description=(
        "Newest event date first. EPS and revenue are decimal strings. "
        "pit_eligibility is the backend's POINT_IN_TIME classification."
    ),
)
def list_earnings_events(
    session: SessionDep, params: Annotated[ListParams, Query()]
) -> EarningsEventPage:
    date_from, date_to, ticker = params.date_from, params.date_to, params.ticker
    limit, offset = params.limit, params.offset
    if date_from is not None and date_to is not None and date_from > date_to:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="date_from must not be after date_to.",
        )
    conditions: list[sa.ColumnElement[bool]] = []
    if date_from is not None:
        conditions.append(EarningsEvent.event_date >= date_from)
    if date_to is not None:
        conditions.append(EarningsEvent.event_date <= date_to)
    try:
        if ticker is not None:
            # The ticker must be PROVEN for the event's company on the event's own date by the same
            # resolver the research layer uses (half-open intervals, conflicts excluded); a listing
            # that started or ended on another date must not rewrite history.
            ranges = HistoricalListingResolver(session).ticker_company_ranges(ticker)
            conditions.append(
                sa.or_(
                    sa.false(),
                    *(
                        sa.and_(
                            EarningsEvent.company_id == company_id,
                            EarningsEvent.event_date >= first,
                            *(() if last is None else (EarningsEvent.event_date < last,)),
                        )
                        for company_id, first, last in ranges
                    ),
                )
            )
        total = session.scalar(
            sa.select(sa.func.count()).select_from(EarningsEvent).where(*conditions)
        )
        events = session.scalars(
            sa.select(EarningsEvent)
            .where(*conditions)
            .order_by(
                EarningsEvent.event_date.desc(),
                EarningsEvent.fiscal_year.desc(),
                EarningsEvent.id,
            )
            .limit(limit)
            .offset(offset)
        ).all()
        company_ids = {event.company_id for event in events}
        listings = _listings_by_company(session, company_ids)
        histories = _Histories(session, company_ids)
        items = [_to_item(event, listings.get(event.company_id, []), histories) for event in events]
    except (OperationalError, InterfaceError) as exc:
        raise _unavailable(exc) from exc
    return EarningsEventPage(items=items, total=total or 0, limit=limit, offset=offset)


@router.get(
    "/{event_id}",
    response_model=EarningsEventItem,
    summary="Get one earnings event",
    responses={status.HTTP_404_NOT_FOUND: {"description": "No such event."}},
)
def get_earnings_event(event_id: uuid.UUID, session: SessionDep) -> EarningsEventItem:
    try:
        event = session.get(EarningsEvent, event_id)
        if event is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found.")
        listings = _listings_by_company(session, {event.company_id})
        return _to_item(
            event, listings.get(event.company_id, []), _Histories(session, {event.company_id})
        )
    except (OperationalError, InterfaceError) as exc:
        raise _unavailable(exc) from exc


__all__ = ["router"]
