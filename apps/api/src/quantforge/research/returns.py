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

Sessions here are EXCHANGE trading sessions from the exchange calendar (Step
11.2E), supplied by the caller as ``forward_sessions`` -- never the dates of
whichever price bars happen to be stored. ``S_N`` is the N-th PROVEN session
after ``S0``; its close is then looked up by exact date. A stored bar that is
absent is a missing PRICE: the horizon is reported unavailable and the target
is never shifted to a later stored bar. A weekend or exchange holiday is a
non-session only because the calendar says so, so it never consumes a horizon
slot, while a missing bar on a real session does make that horizon unavailable.
A close-to-close return needs only the anchor and target closes, so an absent
bar on an INTERIOR session does not change or invalidate a return whose two
endpoint bars exist.

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
from collections.abc import Sequence
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
    "validate_horizons",
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

    #: The exchange calendar cannot prove ``horizon`` sessions after the anchor
    #: (calendar range or venue limit), so the N-th target session cannot even
    #: be identified. ``target_session`` is ``None``. This status says nothing
    #: about stored prices; a proven target with no stored price is
    #: :attr:`MISSING_TARGET_PRICE`.
    INSUFFICIENT_FORWARD_SESSIONS = "INSUFFICIENT_FORWARD_SESSIONS"

    #: The N-th target session IS proven by the exchange calendar, but no price
    #: observation is stored for it (not ingested yet, or genuinely absent --
    #: the stored evidence cannot tell a future session from an un-ingested old
    #: one, and this module never reads the clock). The exact proven session is
    #: reported in ``target_session`` with no close; the target is never moved
    #: to a later stored bar.
    MISSING_TARGET_PRICE = "MISSING_TARGET_PRICE"

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


def validate_horizons(horizons: Sequence[int]) -> None:
    """Reject an unsupported horizon list at the function boundary.

    The contract is: a non-empty sequence of positive ``int`` session counts
    (``bool`` is not accepted as an int). Called before any business logic so
    that validation never depends on which ordinary outcome (no alignment,
    missing price, ...) a call would otherwise have taken. There is no
    horizon-0 or negative semantics.

    Raises:
        ValueError: ``horizons`` is empty, or contains a non-integer or a
            value below 1.
    """
    if len(horizons) == 0:
        message = "horizons must contain at least one positive number of sessions"
        raise ValueError(message)
    values: Sequence[object] = horizons  # runtime guard: callers may not honour the annotation
    for horizon in values:
        if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon < 1:
            message = f"horizon must be a positive integer number of sessions, got {horizon!r}"
            raise ValueError(message)


def compute_forward_returns(
    *,
    anchor_session: date,
    price_series: list[tuple[date, Decimal]],
    horizons: list[int],
    forward_sessions: Sequence[date],
    forward_sessions_reason: str = "",
) -> list[ForwardReturn]:
    """Compute close-to-close forward returns from an anchor to PROVEN session targets.

    Args:
        anchor_session: ``S0`` -- the session a return is measured from. Must
            be a real alignment ``first_post_event_session``, not re-derived
            here.
        forward_sessions: The exchange sessions strictly after ``anchor_session``,
            in order (``forward_sessions[N-1]`` is the N-th). Supplied by the
            caller from the exchange calendar; this function never derives
            sessions from ``price_series``. May be shorter than the largest
            horizon when the calendar could not prove more.
        forward_sessions_reason: Why ``forward_sessions`` is short, if it is.
        price_series: ``(trading_date, close)`` pairs from a single, coherent
            price source (see :mod:`quantforge.research.price_series`) for
            the relevant listing. Used only to look closes up by exact date;
            it never defines which dates are sessions. A coherent single-source
            series cannot contain one date twice (the database's own
            ``(listing_id, trading_date, source_id, price_basis)`` uniqueness
            constraint), so a duplicate is a caller bug this function does not
            paper over.
        horizons: The number of sessions after ``anchor_session`` to measure
            to; validated by :func:`validate_horizons` before anything else.
            Every element of this list, no matter the outcome, produces
            exactly one :class:`ForwardReturn`, in the same order.

    Returns:
        One :class:`ForwardReturn` per element of ``horizons``, in order.
    """
    validate_horizons(horizons)
    price_by_date: dict[date, Decimal] = dict(price_series)

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
    # the *next* session, never S0 itself. The caller supplies them from the
    # exchange calendar; a malformed sequence is a programming error.
    proven = list(forward_sessions)
    if any(
        later <= earlier for earlier, later in zip([anchor_session, *proven], proven, strict=False)
    ):
        message = "forward_sessions must be strictly increasing and strictly after the anchor"
        raise ValueError(message)

    results: list[ForwardReturn] = []
    for horizon in horizons:
        if len(proven) < horizon:
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
                        f"the exchange calendar proves fewer than {horizon} trading sessions "
                        f"after the anchor session {anchor_session}"
                        + (f" ({forward_sessions_reason})" if forward_sessions_reason else "")
                    ),
                )
            )
            continue

        target_session = proven[horizon - 1]
        target_close = price_by_date.get(target_session)
        if target_close is None:
            results.append(
                ForwardReturn(
                    horizon=horizon,
                    anchor_session=anchor_session,
                    anchor_close=anchor_close,
                    target_session=target_session,
                    target_close=None,
                    return_value=None,
                    status=ForwardReturnStatus.MISSING_TARGET_PRICE,
                    reason=(
                        f"{target_session} is the {horizon}-th exchange session after the anchor "
                        f"{anchor_session}, but no price observation is stored for it; the "
                        "target is not moved to another date"
                    ),
                )
            )
            continue

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
                reason="computed from the closes on the anchor and the proven target exchange session",
            )
        )

    return results
