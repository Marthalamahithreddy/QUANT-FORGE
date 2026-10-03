"""Listing-history CLI wiring and exit codes (no database, no network)."""

from __future__ import annotations

import pytest

from quantforge.core.config import Settings
from quantforge.ingestion import cli
from quantforge.ingestion.listing_history import (
    IncompleteListingHistoryError,
    ListingFactConflictError,
)
from quantforge.ingestion.splits import ListingHistoryConflictError


def _parse(*argv: str):  # type: ignore[no-untyped-def]
    return cli.build_parser().parse_args(["ingestion", *argv])


def test_the_three_explicit_operations_are_wired() -> None:
    snapshot = _parse("listing-snapshot", "meta", "--date", "2020-01-02")
    events = _parse("listing-events", "meta")
    eras = _parse("listing-eras", "meta", "--start", "2018-01-01", "--end", "2018-12-31")

    assert snapshot.ticker == "META" and snapshot.delisted is False
    assert events.anchor_date is None
    assert (eras.start.isoformat(), eras.end.isoformat()) == ("2018-01-01", "2018-12-31")
    assert snapshot.handler is cli._cmd_listing_snapshot
    assert events.handler is cli._cmd_listing_events
    assert eras.handler is cli._cmd_listing_eras


@pytest.mark.parametrize(
    "argv", [["listing-snapshot", "META"], ["listing-snapshot", "A", "B", "--date", "2020-01-02"]]
)
def test_a_single_ticker_and_an_explicit_date_are_required(argv: list[str]) -> None:
    with pytest.raises(SystemExit):
        _parse(*argv)


def test_a_reversed_era_report_range_is_a_usage_error() -> None:
    args = _parse("listing-eras", "META", "--start", "2020-02-01", "--end", "2020-01-01")
    assert cli._cmd_listing_eras(args, Settings(_env_file=None)) == cli.EXIT_USAGE


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (IncompleteListingHistoryError("x"), cli.EXIT_RUN_FAILED),
        (ListingFactConflictError("x"), cli.EXIT_RUN_FAILED),
        (ListingHistoryConflictError("x"), cli.EXIT_USAGE),
    ],
)
def test_failures_map_to_distinct_exit_codes(error: Exception, code: int) -> None:
    assert cli._classify(error) == code


def test_the_snapshot_command_needs_the_key_before_any_work_starts() -> None:
    args = _parse("listing-snapshot", "META", "--date", "2020-01-02")
    with pytest.raises(Exception, match="QF_MASSIVE_API_KEY"):
        cli._cmd_listing_snapshot(args, Settings(_env_file=None, massive_api_key=None))
