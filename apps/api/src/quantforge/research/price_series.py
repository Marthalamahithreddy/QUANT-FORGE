"""Fetch a single, coherent close-price series for forward-return calculation.

A listing can legitimately have :class:`~quantforge.models.market.DailyPriceBar`
rows from more than one :class:`~quantforge.models.provenance.DataSource`:
the uniqueness key is ``(listing_id, trading_date, source_id)``, not
``(listing_id, trading_date)`` (see :mod:`quantforge.models.market`'s own
docstring), specifically so two vendors disagreeing about the same session
both survive as separate rows rather than one silently overwriting the other.

:meth:`~quantforge.repositories.market.MarketDataRepository.list_range` and
:meth:`~quantforge.repositories.market.MarketDataRepository.get_for_date`
already handle that for a *single point in time*: when no source is
specified, they break ties by ``ingested_at`` descending so the most recent
restatement wins for *that one date*. That policy is correct for "what is the
best-known price for this one session", but it is the wrong tool here: applied
independently date by date across a forward-return window, it could pick
vendor A's close for the anchor session and vendor B's close for a target
session ten sessions later, simply because each vendor happened to ingest its
own bar for its own date most recently. A return computed from two different
vendors' closes is not one coherent measurement, so this module does not reuse
that per-date tiebreak.

The policy here is instead: resolve one source for the *whole* window a
return calculation needs, and only automatically when there is no real choice
to make (exactly one source has data in that window). The moment two sources
both cover the window, that is reported as an explicit ambiguity
(``AMBIGUOUS_PRICE_SOURCE``) rather than resolved by an arbitrary tiebreak --
a caller that already knows which vendor it wants can always pass
``source_id`` explicitly and skip the ambiguity check entirely.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.orm import Session

from quantforge.models.market import DailyPriceBar

__all__ = ["PriceSeriesResolution", "PriceSeriesStatus", "fetch_coherent_price_series"]


class PriceSeriesStatus(StrEnum):
    """The outcome of resolving one coherent price series for a window."""

    #: A single source's series was resolved; ``series`` is populated.
    AVAILABLE = "AVAILABLE"

    #: No price bar exists for this listing in the requested window, from any
    #: source.
    NO_PRICE_DATA = "NO_PRICE_DATA"

    #: More than one source reports prices for this listing in the requested
    #: window and no ``source_id`` was given to disambiguate. See the module
    #: docstring for why this is refused rather than resolved by a per-date
    #: tiebreak.
    AMBIGUOUS_PRICE_SOURCE = "AMBIGUOUS_PRICE_SOURCE"


@dataclass(frozen=True, slots=True)
class PriceSeriesResolution:
    """The result of resolving a coherent close-price series for one listing/window."""

    status: PriceSeriesStatus
    source_id: uuid.UUID | None
    series: list[tuple[date, Decimal]]
    reason: str


def _closes_from_source(
    session: Session,
    *,
    listing_id: uuid.UUID,
    source_id: uuid.UUID,
    start: date,
    end: date,
) -> list[tuple[date, Decimal]]:
    statement = (
        sa.select(DailyPriceBar.trading_date, DailyPriceBar.close)
        .where(
            DailyPriceBar.listing_id == listing_id,
            DailyPriceBar.source_id == source_id,
            DailyPriceBar.trading_date >= start,
            DailyPriceBar.trading_date <= end,
        )
        .order_by(DailyPriceBar.trading_date)
    )
    return [(row.trading_date, row.close) for row in session.execute(statement)]


def fetch_coherent_price_series(
    session: Session,
    *,
    listing_id: uuid.UUID,
    start: date,
    end: date,
    source_id: uuid.UUID | None = None,
) -> PriceSeriesResolution:
    """Resolve one source's close-price series for a listing over ``[start, end]``.

    Args:
        session: Database session to read from.
        listing_id: The listing to read prices for -- the one Step 8.2
            already resolved for the relevant historical date, never
            re-resolved by today's ticker.
        start: Inclusive lower bound on ``trading_date``.
        end: Inclusive upper bound on ``trading_date``.
        source_id: Use this source's bars only, skipping the ambiguity check
            entirely. When omitted, the window's bars are inspected: a single
            distinct source resolves automatically, more than one is an
            explicit ``AMBIGUOUS_PRICE_SOURCE``.

    Returns:
        A :class:`PriceSeriesResolution`. Never raises for an ordinary "no
        data" or "ambiguous" outcome -- those are reported in the result, not
        as exceptions, consistent with
        :func:`~quantforge.research.listing_resolution.resolve_company_listing_for_date`.
    """
    if source_id is not None:
        series = _closes_from_source(
            session, listing_id=listing_id, source_id=source_id, start=start, end=end
        )
        if not series:
            return PriceSeriesResolution(
                status=PriceSeriesStatus.NO_PRICE_DATA,
                source_id=source_id,
                series=[],
                reason=f"no price bars from source {source_id} for this listing in [{start}, {end}]",
            )
        return PriceSeriesResolution(
            status=PriceSeriesStatus.AVAILABLE,
            source_id=source_id,
            series=series,
            reason="resolved from the explicitly requested source",
        )

    distinct_sources = list(
        session.scalars(
            sa.select(DailyPriceBar.source_id)
            .where(
                DailyPriceBar.listing_id == listing_id,
                DailyPriceBar.trading_date >= start,
                DailyPriceBar.trading_date <= end,
            )
            .distinct()
        )
    )

    if not distinct_sources:
        return PriceSeriesResolution(
            status=PriceSeriesStatus.NO_PRICE_DATA,
            source_id=None,
            series=[],
            reason=f"no price bars exist for this listing in [{start}, {end}]",
        )

    if len(distinct_sources) > 1:
        return PriceSeriesResolution(
            status=PriceSeriesStatus.AMBIGUOUS_PRICE_SOURCE,
            source_id=None,
            series=[],
            reason=(
                f"{len(distinct_sources)} distinct price sources cover this window; "
                "pass an explicit source_id to disambiguate"
            ),
        )

    sole_source = distinct_sources[0]
    series = _closes_from_source(
        session, listing_id=listing_id, source_id=sole_source, start=start, end=end
    )
    return PriceSeriesResolution(
        status=PriceSeriesStatus.AVAILABLE,
        source_id=sole_source,
        series=series,
        reason="exactly one price source covers this window",
    )
