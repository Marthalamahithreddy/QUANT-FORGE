"""The ``sec-filing-acceptance`` command: ONE accession, no bulk or enrichment-all mode."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

import pytest

from quantforge.ingestion import cli
from quantforge.ingestion.filing_acceptance import (
    AcceptanceHeaderMismatchError,
    FilingNotStoredError,
    InvalidAcceptanceRequestError,
    SecAcceptanceConflictError,
)


def _parse(*argv: str) -> argparse.Namespace:
    return cli.build_parser().parse_args(["ingestion", "sec-filing-acceptance", *argv])


def _subparser() -> argparse.ArgumentParser:
    parser = cli.build_parser()
    (top,) = (a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    (ingestion,) = (
        a for a in top.choices["ingestion"]._actions if isinstance(a, argparse._SubParsersAction)
    )
    chosen: argparse.ArgumentParser = ingestion.choices["sec-filing-acceptance"]
    return chosen


def test_the_command_takes_one_accession_an_optional_cik_and_verify() -> None:
    args = _parse("0000320187-26-000037", "--cik", "320187")
    assert (args.accession, args.cik, args.verify) == ("0000320187-26-000037", "320187", False)
    assert args.handler is cli._cmd_sec_filing_acceptance
    assert _parse("0000320187-26-000037", "--verify").verify is True


def test_exactly_one_accession_is_required_and_accepted() -> None:
    with pytest.raises(SystemExit):
        _parse()
    with pytest.raises(SystemExit):
        _parse("0000320187-26-000037", "0000320187-26-000038")


def test_the_option_surface_has_no_bulk_mode() -> None:
    parser = _subparser()
    options = {s for action in parser._actions for s in action.option_strings}
    assert options == {"-h", "--help", "--cik", "--verify"}
    assert [a.dest for a in parser._actions if not a.option_strings] == ["accession"]


def test_there_is_no_enrich_everything_path_anywhere() -> None:
    src = Path(cli.__file__).resolve().parents[1]
    for flag in ("--all", "--enrich-all", "--missing", "--backfill", "--recent", "--company"):
        for relative in ("ingestion/filing_acceptance.py", "providers/sec/header.py"):
            assert flag not in (src / relative).read_text(encoding="utf-8"), (flag, relative)
    cli_text = (src / "ingestion/cli.py").read_text(encoding="utf-8")
    block = cli_text[
        cli_text.index("def _add_filing_acceptance_command") : cli_text.index(
            "def _cmd_sec_filing_acceptance"
        )
    ]
    for flag in ("--all", "--enrich-all", "--missing", "--backfill", "--recent", "--company"):
        assert flag not in block
    # bulk history ingestion never calls the enrichment service
    history = (src / "ingestion/filing_history.py").read_text(encoding="utf-8")
    assert "filing_acceptance" not in history and "get_filing_header" not in history
    filings = (src / "ingestion/filings.py").read_text(encoding="utf-8")
    assert "filing_acceptance" not in filings and "get_filing_header" not in filings


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (FilingNotStoredError("x"), cli.EXIT_USAGE),
        (InvalidAcceptanceRequestError("x"), cli.EXIT_USAGE),
        (AcceptanceHeaderMismatchError("x"), cli.EXIT_RUN_FAILED),
    ],
)
def test_failures_map_to_exit_codes(error: Exception, code: int) -> None:
    assert cli._classify(error) == code


def test_a_conflict_says_nothing_was_changed(capsys: pytest.CaptureFixture[str]) -> None:
    conflict = SecAcceptanceConflictError(
        accession_number="0000320187-26-000037",
        stored=datetime(2026, 4, 1, 20, 16, 39, tzinfo=UTC),
        header=datetime(2026, 4, 1, 21, 16, 39, tzinfo=UTC),
    )
    assert cli._classify(conflict) == cli.EXIT_RUN_FAILED
    assert "accepted_at was not changed" in capsys.readouterr().err
