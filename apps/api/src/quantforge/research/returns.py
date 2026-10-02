"""Deterministic close-to-close forward returns, anchored to Step 8.1/8.2 alignment.

This is an **event-study** return convention, not an executable strategy's
P&L. Given an anchor session ``S0`` (QuantForge's own ``first_post_event_session``
from :mod:`quantforge.research.calendar`) and its close ``P0``, horizon
``N``'s forward return is::

    S_N = the N-th available trading session strictly after S0
    P_N = close(S_N)
    return_N = P_N / P0 - 1

``N=1`` is the *next* available session after ``S0``, never ``S0`` itself --
``S0`` is the anchor, not the first observation in the forward window. This
measures what happened to the close price *after* the anchor session, which is
appropriate for retrospectively characterising an event's aftermath. It is
**not** a claim that a strategy could buy at ``P0`` and sell at ``P_N``: ``P0``
is a closing price, not an executable entry price, and a later backtesting
layer that needs real execution-price semantics (open prices, slippage, fill
timing) is free to define its own anchor without disturbing this convention.

Sessions here mean exactly what they mean in
:mod:`quantforge.research.calendar`: "the N-th available trading session this
function was handed", derived from whatever price series the caller supplied
(see :mod:`quantforge.research.price_series` for where that series comes
from, under the current data architecture that is ``DailyPriceBar`` rows
QuantForge has actually ingested). A gap in that series is never assumed to be
a weekend or a holiday and is never assumed to be anything else either -- it
is just absent, and a horizon landing past the end of the supplied series is
reported as ``INSUFFICIENT_FORWARD_SESSIONS``, not silently shortened or
forward-filled.

These are **split-adjusted price returns, dividends excluded**
-----------------------------------------------------------------
``price_series`` here is ``DailyPriceBar.close``, not ``.adjusted_close``.
Massive's daily-aggregate request (``quantforge.providers.massive.client``)
sends ``adjusted=true``, and Massive is "formerly Polygon.io" using Polygon's
own aggregates contract, where that parameter controls *split* adjustment
only. So ``close`` already *is* the provider's split-adjusted price -- not a
raw, as-traded one -- and it is **not** a total return: cash dividends are not
reflected at all. ``adjusted_close`` stays unconditionally ``NULL`` under the
current ingestion path (``quantforge.ingestion.daily``) and must not be
confused with this; see that column's own documentation for why it is left
``NULL`` rather than populated with a duplicate of ``close``.

Concretely: a stock split is already compensated for in the series this
module reads, so a horizon spanning one will not show a spurious price jump.
A horizon spanning an ex-dividend date will still show the ordinary downward
price adjustment the market makes on that date -- that movement is real, not
an artefact -- but the shareholder's cash distribution itself is excluded
from the return figure, because dividends are not part of what "split-adjusted
close" means. A later total-return calculation would need to add dividend cash
flows back in explicitly; this module does not attempt to, and does not claim
to.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

__all__ = [
    "EventForwardReturns",
    "ForwardReturn",
    "ForwardReturnStatus",
    "compute_forward_returns",
    "no_alignment_forward_returns",
]


class ForwardReturnStatus(StrEnum):
    """Why a forward return could, or honestly could not, be computed."""

    #: The target session and both prices were found and are valid; ``return_value``
    #: is populated.
    AVAILABLE = "AVAILABLE"

    #: The earnings event never produced a usable anchor session (Step 8.2
    #: ``AlignmentStatus`` was not ``ALIGNED``, or alignment could not even be
    #: attempted because no listing resolved). Set only by
    #: :func:`no_alignment_forward_returns` / the orchestration layer in
    #: :mod:`quantforge.research.event_returns`, never by
    #: :func:`compute_forward_returns`, which always receives a real anchor.
    NO_ALIGNMENT = "NO_ALIGNMENT"

    #: No price observation exists for the anchor session itself, so there is
    #: no ``P0`` to divide by.
    MISSING_ANCHOR_PRICE = "MISSING_ANCHOR_PRICE"

    #: Fewer than ``horizon`` trading sessions exist after the anchor in the
    #: supplied series. This means "not enough *stored* sessions were found",
    #: not "the market was closed" -- see the module and
    #: :mod:`quantforge.research.price_series` docstrings on what "available
    #: session" means under the current architecture.
    INSUFFICIENT_FORWARD_SESSIONS = "INSUFFICIENT_FORWARD_SESSIONS"

    #: The anchor close, or the resolved target close, is not a positive
    #: price (``<= 0``). Zero and negative prices are data corruption, not
    #: real prints -- see ``DailyPriceBar``'s own non-negativity constraints --
    #: and dividing by one would either raise or silently produce nonsense.
    INVALID_PRICE = "INVALID_PRICE"

    #: More than one :class:`~quantforge.models.provenance.DataSource`
    #: reports prices for this listing across the window needed for this
    #: horizon, and none was specified explicitly. Set by
    #: :mod:`quantforge.research.price_series` / the orchestration layer,
    #: never by this module's own arithmetic. Refusing to pick one arbitrarily
    #: is what prevents ``P0`` and ``P_N`` from silently coming from two
    #: different vendors' methodologies.
    AMBIGUOUS_PRICE_SOURCE = "AMBIGUOUS_PRICE_SOURCE"


@dataclass(frozen=True, slots=True)
class ForwardReturn:
    """One horizon's forward-return outcome, with enough context to audit it.

    Always produced for every requested horizon, regardless of outcome --
    callers must never have to distinguish "this horizon was silently
    skipped" from "this horizon failed"; there is no such thing as silently
    skipped here.
    """

    horizon: int
    anchor_session: date | None
    anchor_close: Decimal | None
    target_session: date | None
    target_close: Decimal | None
    return_value: Decimal | None
    status: ForwardReturnStatus
    reason: str


@dataclass(frozen=True, slots=True)
class EventForwardReturns:
    """Forward returns for one earnings event across every requested horizon."""

    event_id: uuid.UUID
    listing_id: uuid.UUID | None
    price_source_id: uuid.UUID | None
    anchor_session: date | None
    forward_returns: list[ForwardReturn]


def no_alignment_forward_returns(
    *,
    event_id: uuid.UUID,
    listing_id: uuid.UUID | None,
    horizons: list[int],
) -> EventForwardReturns:
    """Build the explicit "no usable anchor" result for every requested horizon.

    Used whenever Step 8.2 alignment did not produce an ``ALIGNED``
    ``first_post_event_session`` -- an unresolved/ambiguous listing, an
    ``UNKNOWN`` or ``DURING_MARKET`` timing, a point-in-time-ineligible
    observation, or no stored session data at all. None of those are reasons
    to guess an anchor; this function's only job is to report the refusal in
    the same shape a successful computation would have used, so a caller
    cannot confuse "no alignment" with "zero return".
    """
    return EventForwardReturns(
        event_id=event_id,
        listing_id=listing_id,
        price_source_id=None,
        anchor_session=None,
        forward_returns=[
            ForwardReturn(
                horizon=horizon,
                anchor_session=None,
                anchor_close=None,
                target_session=None,
                target_close=None,
                return_value=None,
                status=ForwardReturnStatus.NO_ALIGNMENT,
                reason="the earnings event did not align to a usable anchor session",
            )
            for horizon in horizons
        ],
    )


def compute_forward_returns(
    *,
    anchor_session: date,
    price_series: list[tuple[date, Decimal]],
    horizons: list[int],
) -> list[ForwardReturn]:
    """Compute close-to-close forward returns from an anchor over an ordered price series.

    Args:
        anchor_session: ``S0`` -- the session a return is measured from. Must
            be a real Step 8.2 ``first_post_event_session``, not re-derived
            here.
        price_series: ``(trading_date, close)`` pairs from a single, coherent
            price source (see :mod:`quantforge.research.price_series`) for
            the relevant listing. Sorted defensively by this function, like
            :func:`quantforge.research.calendar.align_event_to_sessions`
            sorts its own session list, but duplicate dates are *not*
            deduplicated here -- a coherent single-source series cannot
            contain one date twice, by the database's own
            ``(listing_id, trading_date, source_id)`` uniqueness constraint,
            so a caller handing in a duplicate has a bug elsewhere, not
            something this function should paper over.
        horizons: The number of sessions after ``anchor_session`` to measure
            to. Every element of this list, no matter the outcome, produces
            exactly one :class:`ForwardReturn`, in the same order.

    Returns:
        One :class:`ForwardReturn` per element of ``horizons``, in order.
    """
    ordered = sorted(price_series, key=lambda row: row[0])
    price_by_date: dict[date, Decimal] = dict(ordered)

    anchor_close = price_by_date.get(anchor_session)
    if anchor_close is None:
        return [
            ForwardReturn(
                horizon=horizon,
                anchor_session=anchor_session,
                anchor_close=None,
                target_session=None,
                target_close=None,
                return_value=None,
                status=ForwardReturnStatus.MISSING_ANCHOR_PRICE,
                reason=f"no price observation exists for the anchor session {anchor_session}",
            )
            for horizon in horizons
        ]

    if anchor_close <= 0:
        return [
            ForwardReturn(
                horizon=horizon,
                anchor_session=anchor_session,
                anchor_close=anchor_close,
                target_session=None,
                target_close=None,
                return_value=None,
                status=ForwardReturnStatus.INVALID_PRICE,
                reason=f"anchor close {anchor_close} is not a valid positive price",
            )
            for horizon in horizons
        ]

    # Only sessions strictly after the anchor count toward a horizon: N=1 is
    # the *next* session, never S0 itself (S0 is the entry point the return is
    # measured from, not the first step of it). Taken from `ordered`, not from
    # iterating the dict, so the result's order does not rely on dict
    # insertion-order semantics -- forward_sessions[h - 1] is the N-th such
    # session directly.
    forward_sessions = [
        session_date for session_date, _ in ordered if session_date > anchor_session
    ]

    results: list[ForwardReturn] = []
    for horizon in horizons:
        if horizon < 1:
            message = f"horizon must be a positive number of sessions, got {horizon}"
            raise ValueError(message)

        if len(forward_sessions) < horizon:
            results.append(
                ForwardReturn(
                    horizon=horizon,
                    anchor_session=anchor_session,
                    anchor_close=anchor_close,
                    target_session=None,
                    target_close=None,
                    return_value=None,
                    status=ForwardReturnStatus.INSUFFICIENT_FORWARD_SESSIONS,
                    reason=(
                        f"fewer than {horizon} available trading sessions exist "
                        f"after the anchor session {anchor_session}"
                    ),
                )
            )
            continue

        target_session = forward_sessions[horizon - 1]
        target_close = price_by_date[target_session]

        if target_close <= 0:
            results.append(
                ForwardReturn(
                    horizon=horizon,
                    anchor_session=anchor_session,
                    anchor_close=anchor_close,
                    target_session=target_session,
                    target_close=target_close,
                    return_value=None,
                    status=ForwardReturnStatus.INVALID_PRICE,
                    reason=f"target close {target_close} on {target_session} is not a valid positive price",
                )
            )
            continue

        results.append(
            ForwardReturn(
                horizon=horizon,
                anchor_session=anchor_session,
                anchor_close=anchor_close,
                target_session=target_session,
                target_close=target_close,
                return_value=target_close / anchor_close - 1,
                status=ForwardReturnStatus.AVAILABLE,
                reason="computed from the ordered available trading-session series",
            )
        )

    return results
