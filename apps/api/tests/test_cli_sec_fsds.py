"""The ``sec-fsds`` command's wiring and exit codes (no database, no network)."""

from __future__ import annotations

from pathlib import Path

import pytest

from quantforge.ingestion import cli
from quantforge.ingestion.financial_facts import (
    AccessionCikMismatchError,
    AccessionNotInPackageError,
    FinancialFactConflictError,
    FsdsRowsRejectedError,
    InvalidFactRequestError,
    MissingSecFilingError,
    NothingToIngestError,
    PackageRevisionError,
    SubmissionMetadataMismatchError,
    UnsupportedFormError,
)
from quantforge.ingestion.raw_store import RawStoreError
from quantforge.providers.sec.fsds import FsdsPackageError


def _parse(*argv: str):  # type: ignore[no-untyped-def]
    return cli.build_parser().parse_args(["ingestion", "sec-fsds", *argv])


def test_the_command_takes_one_package_and_repeatable_selectors() -> None:
    args = _parse(
        "2025q3.zip",
        "--accession",
        "0000320193-24-000010",
        "--accession",
        "0000320193-24-000020",
        "--cik",
        "320193",
    )
    assert args.package == Path("2025q3.zip")
    assert args.accession == ["0000320193-24-000010", "0000320193-24-000020"]
    assert args.cik == ["320193"]
    assert args.package_name is None
    assert args.handler is cli._cmd_sec_fsds


def test_a_package_path_is_required() -> None:
    with pytest.raises(SystemExit):
        _parse("--accession", "0000320193-24-000010")


def test_there_is_no_whole_archive_or_whole_package_switch() -> None:
    for flag in ("--all", "--archive", "--year", "--download", "--url"):
        with pytest.raises(SystemExit):
            _parse("2025q3.zip", flag)


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (InvalidFactRequestError("x"), cli.EXIT_USAGE),
        (UnsupportedFormError("x"), cli.EXIT_USAGE),
        (AccessionNotInPackageError("x"), cli.EXIT_USAGE),
        (MissingSecFilingError("x"), cli.EXIT_USAGE),
        (NothingToIngestError("x"), cli.EXIT_USAGE),
        (FsdsPackageError("x"), cli.EXIT_USAGE),
        (
            AccessionCikMismatchError(accession_number="a", source_cik="1", stored_ciks=["2"]),
            cli.EXIT_RUN_FAILED,
        ),
        (
            SubmissionMetadataMismatchError(accession_number="a", differences={"form": ("a", "b")}),
            cli.EXIT_RUN_FAILED,
        ),
        (
            PackageRevisionError(
                artifact_name="2024q2", new_sha256="a" * 64, known_sha256=["b" * 64]
            ),
            cli.EXIT_RUN_FAILED,
        ),
        (FsdsRowsRejectedError(count=1, samples=["x"]), cli.EXIT_RUN_FAILED),
        (
            FinancialFactConflictError(
                accession_number="a", identity=(), stored=None, incoming=None
            ),
            cli.EXIT_RUN_FAILED,
        ),
    ],
)
def test_failures_map_to_distinct_exit_codes(error: Exception, code: int) -> None:
    assert cli._classify(error) == code


def test_a_raw_store_failure_gets_its_own_accurate_message(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli._classify(RawStoreError("disk full")) == cli.EXIT_RUN_FAILED
    err = capsys.readouterr().err
    assert "could not be read or preserved" in err
    assert "reconcile" not in err
