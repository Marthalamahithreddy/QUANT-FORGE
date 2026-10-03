"""The ``stock-splits`` command's wiring and its guard rails (no database, no network)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from quantforge.core.config import Settings
from quantforge.ingestion import cli
from quantforge.ingestion.splits import (
    AmbiguousSplitSecurityError,
    IncompleteSplitFetchError,
    InvalidSplitRangeError,
    RetiredTickerCoverageError,
    ReusedTickerRangeError,
    StockSplitConflictError,
    UnknownSplitTickerError,
    UnmappedSplitError,
)


def _parse(*argv: str):  # type: ignore[no-untyped-def]
    return cli.build_parser().parse_args(["ingestion", "stock-splits", *argv])


def test_the_command_is_wired_with_a_bounded_range_and_one_ticker() -> None:
    args = _parse("nvda", "--start", "2024-01-01", "--end", "2024-12-31")
    assert args.ticker == "NVDA"
    assert (args.start, args.end) == (date(2024, 1, 1), date(2024, 12, 31))
    assert args.full_history_plan is False
    assert args.handler is cli._cmd_stock_splits


@pytest.mark.parametrize(
    "argv",
    [["NVDA"], ["NVDA", "--start", "2024-01-01"], ["--start", "2024-01-01", "--end", "2024-02-01"]],
)
def test_a_ticker_and_both_dates_are_required(argv: list[str]) -> None:
    with pytest.raises(SystemExit):
        _parse(*argv)


def test_several_tickers_are_not_accepted_so_it_cannot_become_a_bulk_download() -> None:
    with pytest.raises(SystemExit):
        _parse("NVDA", "AAPL", "--start", "2024-01-01", "--end", "2024-02-01")


def test_a_reversed_range_is_a_usage_error() -> None:
    args = _parse("NVDA", "--start", "2024-02-01", "--end", "2024-01-01")
    assert cli._cmd_stock_splits(args, Settings(_env_file=None)) == cli.EXIT_USAGE


def test_a_start_older_than_the_basic_plan_history_is_refused_without_an_attestation() -> None:
    old = (
        datetime.now(UTC).date() - timedelta(days=cli._BASIC_PLAN_SPLIT_HISTORY_DAYS + 30)
    ).isoformat()
    recent_end = (datetime.now(UTC).date() - timedelta(days=8)).isoformat()
    args = _parse("NVDA", "--start", old, "--end", recent_end)
    settings = Settings(_env_file=None, massive_api_key="test-key-0123456789")

    with pytest.raises(InvalidSplitRangeError, match="provider plan"):
        cli._cmd_stock_splits(args, settings)
    assert cli._classify(InvalidSplitRangeError("x")) == cli.EXIT_USAGE


def test_the_full_history_attestation_passes_no_history_bound() -> None:
    args = _parse("NVDA", "--start", "2010-01-01", "--end", "2011-01-01", "--full-history-plan")
    assert args.full_history_plan is True


def test_the_key_is_required_before_any_work_starts() -> None:
    recent = (datetime.now(UTC).date() - timedelta(days=60)).isoformat()
    yesterday = (datetime.now(UTC).date() - timedelta(days=8)).isoformat()
    args = _parse("NVDA", "--start", recent, "--end", yesterday)
    with pytest.raises(Exception, match="QF_MASSIVE_API_KEY"):
        cli._cmd_stock_splits(args, Settings(_env_file=None, massive_api_key=None))


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (InvalidSplitRangeError("x"), cli.EXIT_USAGE),
        (UnknownSplitTickerError("x"), cli.EXIT_USAGE),
        (RetiredTickerCoverageError("x"), cli.EXIT_USAGE),
        (ReusedTickerRangeError("x"), cli.EXIT_USAGE),
        (AmbiguousSplitSecurityError("x"), cli.EXIT_USAGE),
        (IncompleteSplitFetchError("x"), cli.EXIT_RUN_FAILED),
        (UnmappedSplitError("x"), cli.EXIT_RUN_FAILED),
        (
            StockSplitConflictError(provider_split_id="E1", differences={"new_shares": (1, 2)}),
            cli.EXIT_RUN_FAILED,
        ),
    ],
)
def test_failures_map_to_distinct_exit_codes_without_a_traceback(
    error: Exception, code: int
) -> None:
    assert cli._classify(error) == code
