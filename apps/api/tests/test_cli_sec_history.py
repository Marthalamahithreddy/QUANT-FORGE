"""The ``sec-filings-history`` command: one explicit CIK, an explicit bound, no whole-SEC mode."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from quantforge.ingestion import cli
from quantforge.ingestion.filing_history import (
    DEFAULT_MAX_HISTORY_FILES,
    InvalidHistoryRequestError,
)
from quantforge.providers.sec.history import HistoryScopeError


def _parse(*argv: str) -> argparse.Namespace:
    return cli.build_parser().parse_args(["ingestion", "sec-filings-history", *argv])


def _subparser() -> argparse.ArgumentParser:
    parser = cli.build_parser()
    (top,) = (a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    (ingestion,) = (
        a for a in top.choices["ingestion"]._actions if isinstance(a, argparse._SubParsersAction)
    )
    chosen: argparse.ArgumentParser = ingestion.choices["sec-filings-history"]
    return chosen


def test_the_command_takes_one_cik_repeatable_targets_and_an_explicit_bound() -> None:
    args = _parse(
        "320193", "--accession", "0000320193-14-000096", "--accession", "0000320193-13-000001"
    )

    assert args.cik == "320193"
    assert args.accession == ["0000320193-14-000096", "0000320193-13-000001"]
    assert args.max_history_files == DEFAULT_MAX_HISTORY_FILES
    assert args.handler is cli._cmd_sec_filings_history
    assert _parse("320193", "--max-history-files", "3").max_history_files == 3


def test_a_cik_is_required_and_exactly_one_is_accepted() -> None:
    with pytest.raises(SystemExit):
        _parse()
    with pytest.raises(SystemExit):
        _parse("320193", "555555")


def test_the_option_surface_is_exactly_cik_accession_and_bound() -> None:
    parser = _subparser()
    options = {s for action in parser._actions for s in action.option_strings}

    assert options == {"-h", "--help", "--accession", "--max-history-files"}
    positionals = [a.dest for a in parser._actions if not a.option_strings]
    assert positionals == ["cik"]


def test_there_is_no_whole_sec_mode_anywhere_in_the_cli_or_the_history_modules() -> None:
    src = Path(cli.__file__).resolve().parents[1]
    for flag in ("--all", "--every", "--universe", "--crawl", "all-companies", "all_companies"):
        for relative in (
            "ingestion/cli.py",
            "ingestion/filing_history.py",
            "providers/sec/history.py",
            "providers/sec/client.py",
        ):
            text = (src / relative).read_text(encoding="utf-8")
            assert flag not in text, (flag, relative)
    with pytest.raises(SystemExit):
        _parse("320193", "--all")
    with pytest.raises(SystemExit):
        _parse("--all")


def test_the_help_states_the_scope_and_that_it_carries_no_availability_meaning() -> None:
    text = (_subparser().description or "") + " ".join(a.help or "" for a in _subparser()._actions)

    assert "ONE explicit CIK" in text or "One explicit CIK" in text
    assert "no availability" in text and "no 'all companies' mode" in text


def test_scope_and_request_errors_are_usage_errors() -> None:
    assert cli._classify(HistoryScopeError("too many")) == cli.EXIT_USAGE
    assert cli._classify(InvalidHistoryRequestError("bad accession")) == cli.EXIT_USAGE
