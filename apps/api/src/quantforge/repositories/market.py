"""Market observation persistence.

Range reads are ordered by ``trading_date`` ascending, which is the order every
downstream consumer (return series, corporate-action alignment, event windows)
needs. Ordering is always part of the query rather than left to the caller,
because an unordered bar read is a silent correctness bug rather than an error.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from quantforge.models.market import DailyPriceBar

__all__ = ["MarketDataRepository"]


class MarketDataRepository:
    """Reads and writes daily price observations."""

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, bar: DailyPriceBar) -> DailyPriceBar:
        """Stage one observation for insertion and return it.

        Does not flush. A batch can be staged and written in a single
        transaction, so a partially ingested day never lands.
        """
        self._session.add(bar)
        return bar

    def get(self, bar_id: uuid.UUID) -> DailyPriceBar | None:
        """Return the observation with this id, or ``None``."""
        return self._session.get(DailyPriceBar, bar_id)

    def get_for_date(
        self,
        *,
        listing_id: uuid.UUID,
        trading_date: date,
        source_id: uuid.UUID | None = None,
    ) -> DailyPriceBar | None:
        """Return the observation for one listing/session, or ``None``.

        ``source_id`` selects which vendor's opinion is wanted. It is optional
        because "the" price for a session is genuinely ambiguous when several
        providers are loaded; the caller states which one it means rather than
        inheriting an arbitrary answer.

        The descending ``ingested_at`` tiebreak only does work when
        ``source_id`` is omitted, where several vendors may have reported the
        same session: it makes the most recent restatement win rather than
        leaving the choice to the planner. When ``source_id`` is given the
        ``(listing, date, source)`` unique constraint already identifies a single
        row and the ordering is a no-op, kept for a single code path.

        Returns:
            The single matching bar, or ``None`` when no source reported that
            session -- a meaningful answer, not an error.
        """
        conditions = [
            DailyPriceBar.listing_id == listing_id,
            DailyPriceBar.trading_date == trading_date,
        ]
        if source_id is not None:
            conditions.append(DailyPriceBar.source_id == source_id)
        return self._session.scalar(
            sa.select(DailyPriceBar).where(*conditions).order_by(DailyPriceBar.ingested_at.desc())
        )

    def list_range(
        self,
        *,
        listing_id: uuid.UUID,
        start: date,
        end: date,
        source_id: uuid.UUID | None = None,
    ) -> list[DailyPriceBar]:
        """Return observations for a listing within an inclusive date range.

        ``start`` and ``end`` are both **inclusive**, unlike the half-open
        validity intervals used by listings. Session ranges are naturally
        inclusive on both ends -- "every session in Q1" includes the last one --
        and mixing the two conventions in one API would be a reliable source of
        off-by-one bugs.

        Results are ordered by ``trading_date`` ascending, and by ``ingested_at``
        descending as a tiebreak so that when several sources are included the
        ordering between them is deterministic rather than arbitrary.
        """
        conditions = [
            DailyPriceBar.listing_id == listing_id,
            DailyPriceBar.trading_date >= start,
            DailyPriceBar.trading_date <= end,
        ]
        if source_id is not None:
            conditions.append(DailyPriceBar.source_id == source_id)
        statement = (
            sa.select(DailyPriceBar)
            .where(*conditions)
            .order_by(DailyPriceBar.trading_date, DailyPriceBar.ingested_at.desc())
        )
        return list(self._session.scalars(statement))

    def sources_for_listing(
        self,
        *,
        listing_id: uuid.UUID,
        start: date,
        end: date,
    ) -> list[uuid.UUID]:
        """Return the distinct source ids contributing observations in a range.

        A provenance question -- "which vendors reported this listing in this
        window?" -- that would otherwise need a distinct query written ad hoc.
        """
        statement = (
            sa.select(DailyPriceBar.source_id)
            .where(
                DailyPriceBar.listing_id == listing_id,
                DailyPriceBar.trading_date >= start,
                DailyPriceBar.trading_date <= end,
            )
            .distinct()
        )
        return list(self._session.scalars(statement))

    @staticmethod
    def close_series(bars: list[DailyPriceBar]) -> list[Decimal]:
        """Return the close series for a chronological list of bars.

        A tiny local convenience that exists to make one rule unavoidable:
        prices leave this layer as ``Decimal`` and never as ``float``. Anything
        that needs a float must convert deliberately at the point of use.
        """
        return [bar.close for bar in bars]
