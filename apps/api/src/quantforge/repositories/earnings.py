"""Earnings event persistence."""

from __future__ import annotations

import uuid
from datetime import date

import sqlalchemy as sa
from sqlalchemy.orm import Session

from quantforge.models.earnings import EarningsEvent

__all__ = ["EarningsRepository"]


class EarningsRepository:
    """Reads and writes reported earnings results.

    No surprise, return or tradability calculation lives here. The repository
    returns source facts; deriving analytics from them belongs to a later
    analytics layer where the methodology is versioned.
    """

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, event: EarningsEvent) -> EarningsEvent:
        """Stage one event for insertion and return it."""
        self._session.add(event)
        return event

    def get(self, event_id: uuid.UUID) -> EarningsEvent | None:
        """Return the event with this id, or ``None``."""
        return self._session.get(EarningsEvent, event_id)

    def list_chronological(
        self,
        *,
        company_id: uuid.UUID,
        start: date | None = None,
        end: date | None = None,
    ) -> list[EarningsEvent]:
        """Return a company's events in chronological order.

        Ordering by ``event_date`` ascending is the whole point: an event
        study consumes this list in time order, and leaving the order to the
        caller means one caller eventually gets it wrong. ``event_date`` is
        used rather than ``event_timestamp`` because the latter is frequently
        absent (a date-only observation): filtering or ordering on it would
        silently drop or mis-order exactly the rows most earnings-calendar
        sources actually produce.

        Args:
            company_id: Issuer whose events to return.
            start: Optional inclusive lower bound on ``event_date``.
            end: Optional inclusive upper bound.

        Both bounds are inclusive, matching the inclusive convention of the
        market-data range reads. ``None`` on a bound means unbounded on that
        side.

        Note there is no uniqueness assumption: several rows may describe the
        same fiscal period (a preliminary release and a final one, or two
        vendors), and all of them are returned.
        """
        conditions = [EarningsEvent.company_id == company_id]
        if start is not None:
            conditions.append(EarningsEvent.event_date >= start)
        if end is not None:
            conditions.append(EarningsEvent.event_date <= end)
        statement = (
            sa.select(EarningsEvent)
            .where(*conditions)
            .order_by(EarningsEvent.event_date, EarningsEvent.fiscal_year.desc())
        )
        return list(self._session.scalars(statement))

    def list_for_fiscal_period(
        self,
        *,
        company_id: uuid.UUID,
        fiscal_year: int,
        fiscal_quarter: int | None = None,
    ) -> list[EarningsEvent]:
        """Return every event recorded for a fiscal period.

        When ``fiscal_quarter`` is ``None`` the query is deliberately
        interpreted as "this issuer's *annual* figures for that year" -- the
        ``IS NULL`` match -- rather than "any quarter of that year". That is a
        real distinction for non-quarterly filers, and guessing the other way
        would silently return quarterly rows for an annual query.
        """
        conditions = [
            EarningsEvent.company_id == company_id,
            EarningsEvent.fiscal_year == fiscal_year,
        ]
        if fiscal_quarter is None:
            conditions.append(EarningsEvent.fiscal_quarter.is_(None))
        else:
            conditions.append(EarningsEvent.fiscal_quarter == fiscal_quarter)
        statement = sa.select(EarningsEvent).where(*conditions).order_by(EarningsEvent.event_date)
        return list(self._session.scalars(statement))

    def list_between_dates(
        self,
        *,
        company_id: uuid.UUID,
        start: date,
        end: date,
    ) -> list[EarningsEvent]:
        """Return events whose calendar date falls in an inclusive day range.

        A convenience for the common "events in this window" question.

        Compared directly against ``event_date`` -- a plain ``DATE`` with no
        time-zone component to widen or guess at, unlike the old
        ``event_timestamp``-based range this replaced. ``event_date`` is
        always populated, so this finds every event in the window regardless
        of whether the source also gave an exact instant.
        """
        statement = (
            sa.select(EarningsEvent)
            .where(
                EarningsEvent.company_id == company_id,
                EarningsEvent.event_date >= start,
                EarningsEvent.event_date <= end,
            )
            .order_by(EarningsEvent.event_date)
        )
        return list(self._session.scalars(statement))
