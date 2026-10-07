"""Step 11.2E: research sessions come from the REAL exchange calendar, never from stored bars.

These tests use the production adapter (``exchange_calendars``) with real US-equity dates, so they
prove the distinction the old stored-bar model could not make: a real exchange holiday does not
consume a horizon slot, while a missing bar on a real session makes that horizon unavailable.
No database and no network.
"""

from __future__ import annotations

import ast
import importlib.metadata
import inspect
from datetime import date
from decimal import Decimal

import pytest

from quantforge.market import calendar as adapter
from quantforge.market.calendar import ExchangeCalendarsSessionCalendar, default_session_calendar
from quantforge.models.enums import EarningsTiming
from quantforge.reproducibility.methodology import COMPONENT_VERSIONS, EXCHANGE_CALENDARS_VERSION
from quantforge.research import alignment as alignment_module
from quantforge.research import calendar as calendar_module
from quantforge.research import event_returns as event_returns_module
from quantforge.research.calendar import (
    AlignmentStatus,
    align_event_to_sessions,
    proven_sessions_after,
)
from quantforge.research.returns import ForwardReturnStatus, compute_forward_returns

_CAL = ExchangeCalendarsSessionCalendar()

# Real XNYS facts (asserted below against the calendar itself, not trusted from memory):
#   Thu 2025-11-27 Thanksgiving (closed); Fri 2025-11-28 half day (OPEN session);
#   Mon 2026-01-19 Martin Luther King Jr. Day (closed); Fri 2026-07-03 Independence Day observed.
_WED_BEFORE_THANKSGIVING = date(2025, 11, 26)
_THANKSGIVING = date(2025, 11, 27)
_HALF_DAY_FRI = date(2025, 11, 28)
_MON_AFTER = date(2025, 12, 1)
_MLK = date(2026, 1, 19)


def test_the_calendar_facts_these_tests_rely_on() -> None:
    assert _CAL.session_for("XNYS", _THANKSGIVING).status.value == "NOT_A_SESSION"
    assert (
        _CAL.session_for("XNYS", _HALF_DAY_FRI).status.value == "AVAILABLE"
    )  # early close is a session
    assert _CAL.session_for("XNYS", _MLK).status.value == "NOT_A_SESSION"
    assert _CAL.session_for("XNYS", date(2026, 7, 3)).status.value == "NOT_A_SESSION"
    assert _CAL.session_for("XNYS", date(2026, 7, 2)).status.value == "AVAILABLE"


def test_amc_the_day_before_thanksgiving_anchors_on_the_half_day_not_the_holiday() -> None:
    result = align_event_to_sessions(
        event_date=_WED_BEFORE_THANKSGIVING,
        timing=EarningsTiming.AFTER_MARKET,
        calendar=_CAL,
        mic="XNYS",
    )
    assert result.status is AlignmentStatus.ALIGNED
    assert result.first_post_event_session == _HALF_DAY_FRI  # Thursday is closed


def test_bmo_on_a_holiday_rolls_to_the_next_real_session() -> None:
    result = align_event_to_sessions(
        event_date=_MLK, timing=EarningsTiming.BEFORE_MARKET, calendar=_CAL, mic="XNYS"
    )
    assert result.event_session is None
    assert result.first_post_event_session == date(2026, 1, 20)


def test_a_real_holiday_does_not_count_toward_a_horizon() -> None:
    proven = proven_sessions_after(
        calendar=_CAL, mic="XNYS", anchor_session=_WED_BEFORE_THANKSGIVING, count=3
    )
    assert proven.sessions == (_HALF_DAY_FRI, _MON_AFTER, date(2025, 12, 2))
    assert _THANKSGIVING not in proven.sessions


def test_holiday_versus_missing_bar_is_the_distinction_the_old_model_could_not_make() -> None:
    anchor = _WED_BEFORE_THANKSGIVING
    proven = proven_sessions_after(calendar=_CAL, mic="XNYS", anchor_session=anchor, count=3)
    full = [
        (anchor, Decimal("100")),
        (_HALF_DAY_FRI, Decimal("101")),
        (_MON_AFTER, Decimal("102")),
        (date(2025, 12, 2), Decimal("103")),
    ]
    # The holiday has no bar and never should: all three horizons are available.
    ok = {
        r.horizon: r
        for r in compute_forward_returns(
            anchor_session=anchor,
            price_series=full,
            horizons=[1, 2, 3],
            forward_sessions=proven.sessions,
        )
    }
    assert all(r.status is ForwardReturnStatus.AVAILABLE for r in ok.values())
    assert ok[1].target_session == _HALF_DAY_FRI
    # A bar missing on a REAL session (Monday 2025-12-01) makes exactly that horizon unavailable.
    missing = [row for row in full if row[0] != _MON_AFTER]
    gap = {
        r.horizon: r
        for r in compute_forward_returns(
            anchor_session=anchor,
            price_series=missing,
            horizons=[1, 2, 3],
            forward_sessions=proven.sessions,
        )
    }
    assert gap[1].status is ForwardReturnStatus.AVAILABLE
    assert gap[2].status is ForwardReturnStatus.MISSING_TARGET_PRICE
    assert gap[2].target_session == _MON_AFTER
    assert gap[3].status is ForwardReturnStatus.AVAILABLE
    assert gap[3].target_session == date(2025, 12, 2)


@pytest.mark.parametrize("mic", ["XNYS", "XNAS", "ARCX"])
def test_every_supported_venue_resolves_and_others_are_refused(mic: str) -> None:
    result = align_event_to_sessions(
        event_date=date(2026, 3, 4), timing=EarningsTiming.AFTER_MARKET, calendar=_CAL, mic=mic
    )
    assert result.status is AlignmentStatus.ALIGNED
    assert result.first_post_event_session == date(2026, 3, 5)


@pytest.mark.parametrize("mic", ["XLON", "XTKS", ""])
def test_an_unsupported_venue_is_refused_not_defaulted_to_nyse(mic: str) -> None:
    result = align_event_to_sessions(
        event_date=date(2026, 3, 4), timing=EarningsTiming.AFTER_MARKET, calendar=_CAL, mic=mic
    )
    assert result.status is AlignmentStatus.NO_SESSION_DATA
    assert result.first_post_event_session is None


# ── structural: no silent fallback to stored bars ───────────────────────────


def _names(module: object) -> set[str]:
    tree = ast.parse(inspect.getsource(module))  # type: ignore[arg-type]
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
        n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
    }


def test_alignment_no_longer_reads_stored_bars() -> None:
    names = _names(alignment_module)
    assert "DailyPriceBar" not in names
    assert "trading_date" not in names


def test_the_pure_alignment_module_takes_no_session_list_and_no_prices() -> None:
    params = inspect.signature(align_event_to_sessions).parameters
    assert "available_sessions" not in params
    assert {"calendar", "mic"} <= set(params)


def test_forward_sessions_is_a_required_argument_so_stored_dates_can_never_stand_in() -> None:
    parameter = inspect.signature(compute_forward_returns).parameters["forward_sessions"]
    assert parameter.default is inspect.Parameter.empty


def test_event_returns_proves_sessions_before_pricing_them() -> None:
    source = inspect.getsource(event_returns_module)
    assert "proven_sessions_after" in source
    assert source.index("proven_sessions_after(") < source.index("compute_forward_returns(")


def test_the_pure_calendar_module_imports_no_calendar_library() -> None:
    names = {
        n.module
        for n in ast.walk(ast.parse(inspect.getsource(calendar_module)))
        if isinstance(n, ast.ImportFrom)
    }
    assert "exchange_calendars" not in names


# ── reproducibility: the library release the methodology label vouches for ──


def test_the_installed_calendar_release_is_the_one_the_methodology_label_vouches_for() -> None:
    """Upgrading exchange_calendars changes session content: bump the label deliberately first."""
    assert importlib.metadata.version("exchange_calendars") == EXCHANGE_CALENDARS_VERSION
    assert COMPONENT_VERSIONS["session_alignment"] == "exchange-calendar-sessions/1"


def test_the_shared_default_calendar_refuses_a_different_library_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A changed holiday table must not run under an unchanged methodology label."""
    adapter._shared_for.cache_clear()
    monkeypatch.setattr(adapter, "SESSION_CALENDAR_RELEASE", "0.0.0")
    try:
        with pytest.raises(adapter.SessionCalendarReleaseError, match=r"pinned to 0\.0\.0"):
            adapter.default_session_calendar()
    finally:
        adapter._shared_for.cache_clear()


def test_the_default_calendar_is_shared_within_a_day() -> None:
    assert default_session_calendar() is default_session_calendar()


def test_a_horizon_beyond_the_real_calendar_is_insufficient_not_a_missing_price() -> None:
    """Step 12B control: when the calendar cannot prove the N-th session there is no target at all.

    The anchor is far outside any calendar range, so no forward session can be proven; the
    status stays INSUFFICIENT_FORWARD_SESSIONS and names no target. A proven session with no
    stored price is the distinct MISSING_TARGET_PRICE (covered in test_research_returns).
    """
    anchor = date(2099, 6, 1)
    proven = proven_sessions_after(calendar=_CAL, mic="XNYS", anchor_session=anchor, count=2)
    assert proven.complete is False
    results = compute_forward_returns(
        anchor_session=anchor,
        price_series=[(anchor, Decimal("100"))],
        horizons=[1, 2],
        forward_sessions=proven.sessions,
        forward_sessions_reason=proven.reason,
    )
    for result in results:
        assert result.status is ForwardReturnStatus.INSUFFICIENT_FORWARD_SESSIONS
        assert result.target_session is None
        assert result.target_close is None
        assert "OUT_OF_CALENDAR_RANGE" in result.reason
