"""The split guard's interval, coverage and precedence semantics, with no database.

The economic rule under test: a holding from entry session E to exit session X
crosses a split effective on S iff ``E < S <= X``. On S all trading is already
adjusted, so entering ON S changes nothing held, while exiting ON S means the
position was held overnight into the adjusted session.
"""

from __future__ import annotations

import random
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from quantforge.market import (
    CorporateActionGuardResult,
    CoverageInterval,
    GuardStatus,
    SplitFact,
    evaluate_split_guard,
)

SECURITY = uuid.uuid4()
OTHER_SECURITY = uuid.uuid4()
D = date  # short alias for readable tables


def _split(on: date, *, security: uuid.UUID = SECURITY, pid: str = "S1") -> SplitFact:
    return SplitFact(
        split_id=uuid.uuid4(),
        security_id=security,
        execution_date=on,
        old_shares=Decimal(1),
        new_shares=Decimal(10),
        adjustment_type="FORWARD_SPLIT",
        provider_split_id=pid,
    )


def _guard(
    entry: date,
    exit_: date,
    *,
    splits: list[SplitFact] | None = None,
    coverage: list[CoverageInterval] | None = None,
) -> CorporateActionGuardResult:
    return evaluate_split_guard(
        security_id=SECURITY,
        start_session=entry,
        end_session=exit_,
        splits=splits or [],
        coverage=coverage
        if coverage is not None
        else [CoverageInterval(D(2000, 1, 1), D(2030, 1, 1))],
    )


ENTRY, EXIT = D(2024, 6, 5), D(2024, 6, 14)  # Wed .. Fri


def test_a_split_before_entry_does_not_block() -> None:
    assert _guard(ENTRY, EXIT, splits=[_split(D(2024, 6, 4))]).status is GuardStatus.CLEAR


def test_a_split_effective_on_the_entry_session_does_not_block() -> None:
    # Entering on the execution date: that session already trades post-split.
    assert _guard(ENTRY, EXIT, splits=[_split(ENTRY)]).status is GuardStatus.CLEAR


def test_a_split_strictly_between_entry_and_exit_blocks() -> None:
    result = _guard(ENTRY, EXIT, splits=[_split(D(2024, 6, 10))])
    assert result.status is GuardStatus.BLOCKED_BY_SPLIT
    assert [s.execution_date for s in result.blocking_splits] == [D(2024, 6, 10)]
    assert "2024-06-10" in result.reason


def test_a_split_effective_on_the_exit_session_blocks() -> None:
    # Held overnight into the first adjusted session, then exited on it.
    assert _guard(ENTRY, EXIT, splits=[_split(EXIT)]).status is GuardStatus.BLOCKED_BY_SPLIT


def test_a_split_the_day_after_entry_blocks() -> None:
    assert (
        _guard(ENTRY, EXIT, splits=[_split(ENTRY + timedelta(days=1))]).status
        is GuardStatus.BLOCKED_BY_SPLIT
    )


def test_a_split_after_exit_does_not_block() -> None:
    assert (
        _guard(ENTRY, EXIT, splits=[_split(EXIT + timedelta(days=1))]).status is GuardStatus.CLEAR
    )


def test_a_same_session_round_trip_holds_nothing_overnight() -> None:
    same = _guard(ENTRY, ENTRY, splits=[_split(ENTRY)], coverage=[])
    assert same.status is GuardStatus.CLEAR
    assert "same session" in same.reason


def test_a_reversed_interval_is_invalid_not_clear() -> None:
    result = _guard(EXIT, ENTRY, splits=[_split(D(2024, 6, 10))])
    assert result.status is GuardStatus.INVALID_INTERVAL


def test_a_weekend_execution_date_is_compared_by_calendar_date_without_shifting() -> None:
    saturday = D(2024, 6, 8)
    assert _guard(D(2024, 6, 7), D(2024, 6, 10), splits=[_split(saturday)]).status is (
        GuardStatus.BLOCKED_BY_SPLIT
    )
    # Exiting Friday, before the weekend split, is untouched.
    assert _guard(D(2024, 6, 5), D(2024, 6, 7), splits=[_split(saturday)]).status is (
        GuardStatus.CLEAR
    )


def test_other_securities_splits_never_block() -> None:
    result = _guard(ENTRY, EXIT, splits=[_split(D(2024, 6, 10), security=OTHER_SECURITY)])
    assert result.status is GuardStatus.CLEAR


def test_multiple_blocking_splits_are_all_reported_in_date_order() -> None:
    result = _guard(
        ENTRY, EXIT, splits=[_split(D(2024, 6, 12), pid="B"), _split(D(2024, 6, 7), pid="A")]
    )
    assert [s.provider_split_id for s in result.blocking_splits] == ["A", "B"]


# ── coverage: absence of a split is not evidence ────────────────────────────


def test_no_coverage_at_all_is_unavailable_not_clear() -> None:
    result = _guard(ENTRY, EXIT, coverage=[])
    assert result.status is GuardStatus.COVERAGE_UNAVAILABLE
    assert result.uncovered_ranges == (CoverageInterval(D(2024, 6, 6), EXIT),)
    assert "not proven" in result.reason


def test_partial_coverage_is_unavailable_and_names_the_gap() -> None:
    result = _guard(ENTRY, EXIT, coverage=[CoverageInterval(D(2024, 6, 1), D(2024, 6, 9))])
    assert result.status is GuardStatus.COVERAGE_UNAVAILABLE
    assert result.uncovered_ranges == (CoverageInterval(D(2024, 6, 10), EXIT),)


def test_coverage_that_starts_late_leaves_the_early_dates_unproven() -> None:
    result = _guard(ENTRY, EXIT, coverage=[CoverageInterval(D(2024, 6, 10), D(2024, 6, 30))])
    assert result.uncovered_ranges == (CoverageInterval(D(2024, 6, 6), D(2024, 6, 9)),)


def test_a_gap_in_the_middle_is_unproven() -> None:
    coverage = [
        CoverageInterval(D(2024, 6, 1), D(2024, 6, 8)),
        CoverageInterval(D(2024, 6, 12), D(2024, 6, 30)),
    ]
    result = _guard(ENTRY, EXIT, coverage=coverage)
    assert result.uncovered_ranges == (CoverageInterval(D(2024, 6, 9), D(2024, 6, 11)),)


def test_touching_ranges_merge_into_full_coverage() -> None:
    coverage = [
        CoverageInterval(D(2024, 6, 1), D(2024, 6, 9)),
        CoverageInterval(D(2024, 6, 10), D(2024, 6, 30)),
    ]
    assert _guard(ENTRY, EXIT, coverage=coverage).status is GuardStatus.CLEAR


def test_coverage_must_cover_the_day_after_entry_through_exit_exactly() -> None:
    # (E, X] = 2024-06-06 .. 2024-06-14. Covering from E itself is not required.
    exact = [CoverageInterval(D(2024, 6, 6), EXIT)]
    assert _guard(ENTRY, EXIT, coverage=exact).status is GuardStatus.CLEAR
    one_short = [CoverageInterval(D(2024, 6, 6), EXIT - timedelta(days=1))]
    assert _guard(ENTRY, EXIT, coverage=one_short).status is GuardStatus.COVERAGE_UNAVAILABLE


def test_full_coverage_and_no_split_is_clear_with_a_reason() -> None:
    result = _guard(ENTRY, EXIT)
    assert result.status is GuardStatus.CLEAR and result.is_clear
    assert "covers" in result.reason


def test_full_coverage_and_a_split_is_blocked() -> None:
    assert _guard(ENTRY, EXIT, splits=[_split(D(2024, 6, 10))]).status is (
        GuardStatus.BLOCKED_BY_SPLIT
    )


def test_a_known_split_blocks_even_without_any_coverage() -> None:
    result = _guard(ENTRY, EXIT, splits=[_split(D(2024, 6, 10))], coverage=[])
    assert result.status is GuardStatus.BLOCKED_BY_SPLIT


def test_the_guard_result_never_carries_an_adjustment() -> None:
    result = _guard(ENTRY, EXIT, splits=[_split(D(2024, 6, 10))])
    assert set(result.__slots__) == {
        "status",
        "security_id",
        "start_session",
        "end_session",
        "blocking_splits",
        "uncovered_ranges",
        "reason",
    }


def test_coverage_interval_rejects_an_inverted_range() -> None:
    with pytest.raises(ValueError, match="precedes"):
        CoverageInterval(D(2024, 2, 1), D(2024, 1, 1))


# ── randomized agreement with a brute-force day-by-day oracle ───────────────


def _oracle(
    entry: date,
    exit_: date,
    split_days: list[date],
    covered_days: set[date],
) -> GuardStatus:
    if exit_ < entry:
        return GuardStatus.INVALID_INTERVAL
    needed = [entry + timedelta(days=i) for i in range(1, (exit_ - entry).days + 1)]
    if any(day in needed for day in split_days):
        return GuardStatus.BLOCKED_BY_SPLIT
    if not needed or all(day in covered_days for day in needed):
        return GuardStatus.CLEAR
    return GuardStatus.COVERAGE_UNAVAILABLE


def test_the_guard_agrees_with_a_day_by_day_oracle_on_random_inputs() -> None:
    rng = random.Random(1066)
    base = D(2024, 1, 1)
    for _ in range(3000):
        entry = base + timedelta(days=rng.randrange(0, 40))
        exit_ = entry + timedelta(days=rng.randrange(-3, 25))
        intervals = []
        for _ in range(rng.randrange(0, 5)):
            start = base + timedelta(days=rng.randrange(0, 70))
            intervals.append(CoverageInterval(start, start + timedelta(days=rng.randrange(0, 20))))
        split_days = [
            base + timedelta(days=rng.randrange(0, 70)) for _ in range(rng.randrange(0, 3))
        ]
        covered = {
            i.start + timedelta(days=k)
            for i in intervals
            for k in range((i.end - i.start).days + 1)
        }

        result = evaluate_split_guard(
            security_id=SECURITY,
            start_session=entry,
            end_session=exit_,
            splits=[_split(day, pid=f"S{n}") for n, day in enumerate(split_days)],
            coverage=intervals,
        )

        assert result.status is _oracle(entry, exit_, split_days, covered), (
            entry,
            exit_,
            split_days,
            intervals,
        )
        if result.status is GuardStatus.COVERAGE_UNAVAILABLE:
            gap_days = {
                gap.start + timedelta(days=k)
                for gap in result.uncovered_ranges
                for k in range((gap.end - gap.start).days + 1)
            }
            needed = {entry + timedelta(days=i) for i in range(1, (exit_ - entry).days + 1)}
            assert gap_days == needed - covered, "the reported gaps are exactly the unproven dates"


def test_overlapping_and_duplicate_coverage_rows_never_shrink_or_invent_coverage() -> None:
    rows = [
        CoverageInterval(D(2024, 6, 1), D(2024, 6, 10)),
        CoverageInterval(D(2024, 6, 1), D(2024, 6, 10)),
        CoverageInterval(D(2024, 6, 5), D(2024, 6, 8)),
    ]
    clear = _guard(D(2024, 6, 1), D(2024, 6, 10), coverage=rows)
    assert clear.status is GuardStatus.CLEAR
    short = _guard(D(2024, 6, 1), D(2024, 6, 11), coverage=rows)
    assert short.status is GuardStatus.COVERAGE_UNAVAILABLE
    assert short.uncovered_ranges == (CoverageInterval(D(2024, 6, 11), D(2024, 6, 11)),)


def test_two_provider_ids_for_one_economic_split_both_block_and_never_clear() -> None:
    twin_a, twin_b = _split(D(2024, 6, 10), pid="A"), _split(D(2024, 6, 10), pid="B")
    result = _guard(ENTRY, EXIT, splits=[twin_a, twin_b])
    assert result.status is GuardStatus.BLOCKED_BY_SPLIT
    assert len(result.blocking_splits) == 2
