"""Stored splits and split coverage, and the guard that reads both.

Reads only. Writing is the ingestion service's job, inside a run, so every row has
provenance. No price, share count or cost basis is read or changed here.
"""

from __future__ import annotations

import uuid
from datetime import date

import sqlalchemy as sa
from sqlalchemy.orm import Session

from quantforge.market.corporate_actions import (
    CorporateActionGuardResult,
    CoverageInterval,
    SplitFact,
    evaluate_split_guard,
)
from quantforge.models.corporate_action import StockSplit, StockSplitCoverage

__all__ = ["StockSplitRepository"]


class StockSplitRepository:
    """Queries over ``stock_split`` and ``stock_split_coverage``."""

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    def splits_between(
        self, security_id: uuid.UUID, *, after: date, through: date
    ) -> list[SplitFact]:
        """Splits of one security taking effect in ``(after, through]``.

        The canonical primitive: strictly after ``after`` (the entry session) and on
        or before ``through`` (the exit session). Ordered by execution date, then
        provider id, so the result is deterministic. Returns the facts, not a
        Boolean, so a refusal can say which split blocked it.
        """
        rows = self._session.scalars(
            sa.select(StockSplit)
            .where(
                StockSplit.security_id == security_id,
                StockSplit.execution_date > after,
                StockSplit.execution_date <= through,
            )
            .order_by(StockSplit.execution_date, StockSplit.provider_split_id)
        )
        return [
            SplitFact(
                split_id=row.id,
                security_id=row.security_id,
                execution_date=row.execution_date,
                old_shares=row.old_shares,
                new_shares=row.new_shares,
                adjustment_type=row.adjustment_type.value,
                provider_split_id=row.provider_split_id,
            )
            for row in rows
        ]

    def coverage_for(
        self, security_id: uuid.UUID, *, source_id: uuid.UUID
    ) -> list[CoverageInterval]:
        """Date ranges for which ``source_id`` completed a clean split fetch for this security.

        Coverage is source-specific on purpose: a source's coverage proves absence only
        according to that source's own split dataset, so one source's fetch can never stand
        in for another's.
        """
        rows = self._session.execute(
            sa.select(StockSplitCoverage.covered_from, StockSplitCoverage.covered_to)
            .where(
                StockSplitCoverage.security_id == security_id,
                StockSplitCoverage.source_id == source_id,
            )
            .order_by(StockSplitCoverage.covered_from, StockSplitCoverage.covered_to)
        )
        return [CoverageInterval(start, end) for start, end in rows]

    def guard(
        self,
        security_id: uuid.UUID,
        *,
        coverage_source_id: uuid.UUID,
        start_session: date,
        end_session: date,
    ) -> CorporateActionGuardResult:
        """Whether holding ``security_id`` from entry to exit session may proceed.

        Source semantics (explicit): a stored split from ANY source blocks, because a known
        split is positive evidence whoever reported it; but absence of a split is proven only
        by coverage from ``coverage_source_id``. Coverage rows of other sources, or of other
        securities, are never counted. See :mod:`quantforge.market.corporate_actions` for the
        ``E < S <= X`` rule and for why an empty split table alone never yields ``CLEAR``.
        """
        splits = (
            self.splits_between(security_id, after=start_session, through=end_session)
            if end_session >= start_session
            else []
        )
        return evaluate_split_guard(
            security_id=security_id,
            start_session=start_session,
            end_session=end_session,
            splits=splits,
            coverage=self.coverage_for(security_id, source_id=coverage_source_id),
        )
