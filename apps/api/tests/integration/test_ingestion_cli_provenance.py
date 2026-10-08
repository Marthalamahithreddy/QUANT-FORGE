"""Step 12C: the earnings command, the failed-run footer and the ``runs`` diagnostics.

The real entry point, services, database, raw store and run table over mocked HTTP transports. The
footer's claims -- the run id, the evidence path, ``written`` and ``safe to retry: yes`` -- are each
checked against the database and the disk, never against the command's own output alone.
"""

from __future__ import annotations

import json
import re
import traceback
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from quantforge.core.config import get_settings
from quantforge.ingestion import cli
from quantforge.ingestion.reference import ReferenceResolver
from quantforge.models.earnings import EarningsEvent
from quantforge.models.enums import IngestionStatus
from quantforge.models.ingestion import IngestionRun
from quantforge.providers.finnhub.client import FinnhubClient
from quantforge.providers.massive.client import MassiveClient

pytestmark = pytest.mark.integration

KEY = "test-key-not-real"
FINNHUB_KEY = "finnhub-key-not-real"

AAPL_REFERENCE: dict[str, Any] = {
    "ticker": "AAPL",
    "name": "Apple Inc.",
    "cik": "0000320193",
    "composite_figi": "BBG000B9XRY4",
    "share_class_figi": "BBG001S5QY8",
    "market": "stocks",
    "locale": "us",
    "primary_exchange": "XNAS",
    "type": "CS",
    "active": True,
    "currency_name": "usd",
}
CALENDAR_OK = (
    b'{"earningsCalendar": [{"symbol": "AAPL", "date": "2099-03-15", "year": 2099, '
    b'"quarter": 1, "epsActual": 1.46, "epsEstimate": 1.4, "hour": "amc"}]}'
)
UNDECODABLE = b"<html>upstream proxy error</html>"
# Dated far ahead on purpose: the CLI stamps a listing's validity from the real clock, and an event
# that predates the listing history is (correctly) refused as unattributable.
ARGV = ["ingestion", "earnings", "AAPL", "--start", "2099-01-01", "--end", "2099-12-31"]

Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture
def empty_database(integration_engine: Engine) -> Iterator[None]:
    names = ", ".join(sa.inspect(integration_engine).get_table_names())
    guard = "research_observation_record"

    def reset() -> None:
        with integration_engine.begin() as connection:
            connection.execute(sa.text(f"ALTER TABLE {guard} DISABLE TRIGGER USER"))
            connection.execute(sa.text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
            connection.execute(sa.text(f"ALTER TABLE {guard} ENABLE TRIGGER USER"))

    reset()
    yield
    reset()


@pytest.fixture
def configured(
    monkeypatch: pytest.MonkeyPatch, integration_engine: Engine, tmp_path: Path
) -> Iterator[Path]:
    raw_root = tmp_path / "raw"
    monkeypatch.setenv(
        "QF_DATABASE_URL", integration_engine.url.render_as_string(hide_password=False)
    )
    monkeypatch.setenv("QF_MASSIVE_API_KEY", KEY)
    monkeypatch.setenv("QF_FINNHUB_API_KEY", FINNHUB_KEY)
    monkeypatch.setenv("QF_INGESTION_RAW_ROOT", str(raw_root))
    get_settings.cache_clear()
    try:
        yield raw_root
    finally:
        get_settings.cache_clear()


@pytest.fixture
def serve(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    """Install mock transports for Massive (reference) and Finnhub (earnings)."""
    real_massive, real_finnhub = cli._client, cli._finnhub_client

    def install(*, massive: Handler | None = None, finnhub: Handler | None = None) -> None:
        if massive is not None:
            monkeypatch.setattr(
                cli,
                "_client",
                lambda settings: MassiveClient(
                    real_massive(settings)._config, transport=httpx.MockTransport(massive)
                ),
            )
        if finnhub is not None:
            monkeypatch.setattr(
                cli,
                "_finnhub_client",
                lambda settings: FinnhubClient(
                    real_finnhub(settings)._config, transport=httpx.MockTransport(finnhub)
                ),
            )

    return install


def _reference_handler(request: httpx.Request) -> httpx.Response:
    if request.url.params.get("ticker") == "AAPL":
        return httpx.Response(200, json={"status": "OK", "results": AAPL_REFERENCE})
    return httpx.Response(404, json={"status": "ERROR"})


def _finnhub(body: bytes, status: int = 200) -> Handler:
    return lambda _request: httpx.Response(status, content=body)


def _runs(engine: Engine, dataset: str) -> list[IngestionRun]:
    with Session(engine) as session:
        rows = list(session.scalars(sa.select(IngestionRun).where(IngestionRun.dataset == dataset)))
        session.expunge_all()
        return rows


def _with_apple(serve: Callable[..., None]) -> None:
    serve(massive=_reference_handler)
    assert cli.main(["ingestion", "reference", "AAPL"]) == cli.EXIT_OK


# ── the command ─────────────────────────────────────────────────────────────


def test_the_earnings_command_is_reachable_and_upper_cases_the_symbol() -> None:
    args = cli.build_parser().parse_args(
        ["ingestion", "earnings", "aapl", "--start", "2026-09-01", "--end", "2026-09-30"]
    )
    assert callable(args.handler) and args.ticker == "AAPL"


def test_an_inverted_range_is_a_usage_error_that_creates_no_run(
    configured: Path,
    empty_database: None,
    integration_engine: Engine,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = cli.main(
        ["ingestion", "earnings", "AAPL", "--start", "2026-10-01", "--end", "2026-09-01"]
    )
    assert code == cli.EXIT_USAGE
    assert "is before --start" in capsys.readouterr().err
    assert _runs(integration_engine, "earnings") == []


@pytest.mark.parametrize(
    "argv",
    [
        ["ingestion", "earnings", "AAPL", "--end", "2026-09-30"],
        ["ingestion", "earnings", "AAPL", "--start", "2026-09-01"],
        ["ingestion", "earnings", "--start", "2026-09-01", "--end", "2026-09-30"],
        ["ingestion", "earnings", "AAPL", "--start", "2026-13-01", "--end", "2026-09-30"],
        ["ingestion", "earnings", "bad symbol", "--start", "2026-09-01", "--end", "2026-09-30"],
    ],
)
def test_a_malformed_invocation_is_refused_by_the_parser(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        cli.main(argv)
    assert caught.value.code == 2


def test_a_missing_finnhub_key_is_a_usage_error_not_a_failed_run(
    configured: Path,
    empty_database: None,
    integration_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("QF_FINNHUB_API_KEY")
    get_settings.cache_clear()
    code = cli.main(ARGV)
    assert code == cli.EXIT_USAGE
    assert "QF_FINNHUB_API_KEY" in capsys.readouterr().err
    assert _runs(integration_engine, "earnings") == []


def test_a_successful_earnings_run_prints_the_run_the_counts_and_the_evidence(
    configured: Path,
    empty_database: None,
    serve: Callable[..., None],
    integration_engine: Engine,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _with_apple(serve)
    serve(finnhub=_finnhub(CALENDAR_OK))
    capsys.readouterr()

    assert cli.main(ARGV) == cli.EXIT_OK

    out = capsys.readouterr().out
    (run,) = _runs(integration_engine, "earnings")
    assert run.status is IngestionStatus.SUCCEEDED
    assert f"run {run.id} [finnhub/earnings] succeeded" in out
    assert f"raw: {run.raw_artifact_path}" in out
    assert "AAPL 2099-01-01..2099-12-31: received=1 written=1 skipped=0 failed=0" in out
    assert run.error_message is None
    manifest = json.loads((configured / (run.raw_artifact_path or "")).read_bytes())
    assert [(configured / a["path"]).read_bytes() for a in manifest["artifacts"]] == [CALENDAR_OK]
    with Session(integration_engine) as session:
        event = session.scalars(sa.select(EarningsEvent)).one()
        assert event.observed_at is None, "Finnhub availability semantics did not move"


def test_a_partial_earnings_run_explains_itself_on_stdout(
    configured: Path,
    empty_database: None,
    serve: Callable[..., None],
    integration_engine: Engine,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _with_apple(serve)
    body = (
        b'{"earningsCalendar": ['
        b'{"symbol": "AAPL", "date": "2099-03-15", "year": 2099, "quarter": 1},'
        b'{"symbol": "ZZZZ", "date": "2099-03-16", "year": 2099, "quarter": 1}]}'
    )
    serve(finnhub=_finnhub(body))
    capsys.readouterr()

    assert cli.main(ARGV) == cli.EXIT_OK, "partial is a success with a shortfall"

    out = capsys.readouterr().out
    assert "partial" in out
    assert "error: 1 record(s) failed; first: ZZZZ 2099-03-16: ZZZZ is not stored." in out


# ── the failure footer ──────────────────────────────────────────────────────


def _footer(err: str) -> dict[str, str]:
    """The ``key: value`` lines of the footer, plus the run line."""
    fields: dict[str, str] = {}
    match = re.search(r"^run (?P<id>[0-9a-f-]{36}) \[(?P<what>[^\]]+)\] failed$", err, re.MULTILINE)
    assert match, err
    fields["id"], fields["what"] = match["id"], match["what"]
    for line in err.splitlines():
        key, sep, value = line.strip().partition(": ")
        if sep and key in {"raw evidence", "written", "reason"}:
            fields[key] = value
    fields["retry"] = "safe to retry: yes" if "safe to retry: yes" in err else ""
    return fields


def test_a_provider_failure_names_the_run_the_evidence_and_that_a_retry_is_safe(
    configured: Path,
    empty_database: None,
    serve: Callable[..., None],
    integration_engine: Engine,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _with_apple(serve)
    serve(finnhub=_finnhub(UNDECODABLE))
    capsys.readouterr()

    assert cli.main(ARGV) == cli.EXIT_RUN_FAILED

    err = capsys.readouterr().err
    footer = _footer(err)
    (run,) = _runs(integration_engine, "earnings")
    assert run.status is IngestionStatus.FAILED
    assert footer["id"] == str(run.id), "the id is the durable failed run's"
    assert footer["what"] == "finnhub/earnings"
    assert footer["raw evidence"] == run.raw_artifact_path
    assert (configured / footer["raw evidence"]).is_file(), "the named evidence exists"
    assert footer["written"] == "0"
    assert footer["retry"] == "safe to retry: yes"
    assert footer["reason"].startswith("malformed provider response")
    assert err.index("error:") < err.index("run "), "the error comes first, the footer after"
    manifest = json.loads((configured / footer["raw evidence"]).read_bytes())
    (entry,) = manifest["artifacts"]
    assert "/earnings-rejected/" in f"/{entry['path']}"
    assert (configured / entry["path"]).read_bytes() == UNDECODABLE


def test_a_failure_with_no_evidence_says_none(
    configured: Path,
    empty_database: None,
    serve: Callable[..., None],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _with_apple(serve)
    serve(finnhub=_finnhub(b'{"echo":"x"}', status=500))
    capsys.readouterr()

    assert cli.main(ARGV) == cli.EXIT_RUN_FAILED

    footer = _footer(capsys.readouterr().err)
    assert footer["raw evidence"] == "none"
    assert footer["written"] == "0"


def test_the_footer_and_the_error_never_carry_a_credential(
    configured: Path,
    empty_database: None,
    serve: Callable[..., None],
    integration_engine: Engine,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _with_apple(serve)
    serve(finnhub=_finnhub(b'{"error":"bad token"}', status=401))
    capsys.readouterr()

    assert cli.main(ARGV) == cli.EXIT_USAGE, "an authentication failure is the operator's to fix"

    captured = capsys.readouterr()
    assert _footer(captured.err)["id"], "the run still exists and is named"
    for text in (captured.out, captured.err):
        assert FINNHUB_KEY not in text and KEY not in text
    (run,) = _runs(integration_engine, "earnings")
    assert FINNHUB_KEY not in (run.error_message or "")


_CREDENTIAL_TEXTS = [
    ("password='Sup3rS3cret with space'", "Sup3rS3cret"),
    ('password="Sup3rS3cret with space"', "Sup3rS3cret"),
    ('{"api_key": "Sup3rS3cretKey99"}', "Sup3rS3cretKey99"),
    ("client_secret=Sup3rS3cretKey99", "Sup3rS3cretKey99"),
    ("(token='Sup3rS3cretKey99')", "Sup3rS3cretKey99"),
]


@pytest.mark.parametrize(("fragment", "secret"), _CREDENTIAL_TEXTS)
def test_a_credential_in_an_unexpected_error_reaches_no_cli_output_and_no_row(
    configured: Path,
    empty_database: None,
    serve: Callable[..., None],
    integration_engine: Engine,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    fragment: str,
    secret: str,
) -> None:
    """Every sink of a failing run: stdout, stderr (error line and footer), the row, `runs`, and the logs."""
    _with_apple(serve)

    def explode(_request: httpx.Request) -> httpx.Response:
        message = f"upstream rejected the call: {fragment} while fetching"
        raise RuntimeError(message)

    serve(finnhub=explode)
    capsys.readouterr()

    # An unrecognised failure is re-raised so its traceback finds the bug; the traceback must not carry the secret.
    with pytest.raises(RuntimeError) as raised:
        cli.main(ARGV)
    assert secret not in str(raised.value)
    assert secret not in "".join(traceback.format_exception(raised.value))
    failing = capsys.readouterr()
    assert cli.main(["ingestion", "runs", "--status", "failed"]) == cli.EXIT_OK
    listing = capsys.readouterr()

    for text in (failing.out, failing.err, listing.out, listing.err, caplog.text):
        assert secret not in text
    assert "upstream rejected the call" in failing.err, "the diagnostic text survives"
    (run,) = _runs(integration_engine, "earnings")
    assert secret not in (run.error_message or "")
    assert "[redacted]" in (run.error_message or "")
    for record in caplog.records:
        assert secret not in record.getMessage()
        if record.exc_info is not None:
            assert secret not in "".join(traceback.format_exception(*record.exc_info))


def test_a_failure_before_any_run_exists_prints_no_footer(
    configured: Path, empty_database: None, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(["ingestion", "daily", "NOPE", "--start", "2026-09-01", "--end", "2026-09-02"])
    assert code == cli.EXIT_USAGE
    err = capsys.readouterr().err
    assert "error:" in err and "safe to retry" not in err


def test_a_reference_failure_footer_works_for_the_other_commands_too(
    configured: Path,
    empty_database: None,
    serve: Callable[..., None],
    integration_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    serve(massive=_reference_handler)

    def explode(self: ReferenceResolver, reference: Any, **kwargs: Any) -> Any:
        message = "unexpected storage failure"
        raise RuntimeError(message)

    monkeypatch.setattr(ReferenceResolver, "resolve", explode)
    with pytest.raises(RuntimeError, match="unexpected storage failure"):
        cli.main(["ingestion", "reference", "AAPL"])
    # an unexpected bug still re-raises with its traceback, but names the failed run first
    err = capsys.readouterr().err
    footer = _footer(err)
    (run,) = _runs(integration_engine, "reference")
    assert footer["id"] == str(run.id)
    assert (configured / footer["raw evidence"]).is_file()
    assert footer["retry"] == "safe to retry: yes"


# ── the retry claim ─────────────────────────────────────────────────────────


def test_retrying_the_commands_12c_changed_never_duplicates(
    configured: Path,
    empty_database: None,
    serve: Callable[..., None],
    integration_engine: Engine,
) -> None:
    """The footer says "safe to retry: yes" -- true because a failed run rolls its writes back and
    every write is idempotent (see the 12A audit, 5.5; daily bars, benchmarks, SEC and splits have
    their own idempotency suites). Pinned here for the two commands 12C changed."""
    serve(massive=_reference_handler)
    serve(finnhub=_finnhub(UNDECODABLE))
    assert cli.main(["ingestion", "reference", "AAPL"]) == cli.EXIT_OK
    assert cli.main(ARGV) == cli.EXIT_RUN_FAILED

    serve(finnhub=_finnhub(CALENDAR_OK))
    assert cli.main(ARGV) == cli.EXIT_OK
    assert cli.main(ARGV) == cli.EXIT_OK
    assert cli.main(["ingestion", "reference", "AAPL"]) == cli.EXIT_OK

    with Session(integration_engine) as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(EarningsEvent)) == 1
        statuses = sorted(
            run.status.value
            for run in session.scalars(
                sa.select(IngestionRun).where(IngestionRun.dataset == "earnings")
            )
        )
        assert statuses == ["failed", "succeeded", "succeeded"]
    skipped = [
        r.records_skipped for r in _runs(integration_engine, "earnings") if r.records_skipped
    ]
    assert skipped == [1], "the second success skipped the identical observation"


# ── runs ────────────────────────────────────────────────────────────────────


def test_runs_can_filter_by_status(
    configured: Path,
    empty_database: None,
    serve: Callable[..., None],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _with_apple(serve)
    serve(finnhub=_finnhub(UNDECODABLE))
    assert cli.main(ARGV) == cli.EXIT_RUN_FAILED
    capsys.readouterr()

    assert cli.main(["ingestion", "runs", "--status", "failed"]) == cli.EXIT_OK
    failed = capsys.readouterr().out
    assert "finnhub/earnings" in failed and "failed" in failed
    assert "massive/reference" not in failed

    assert cli.main(["ingestion", "runs", "--status", "succeeded"]) == cli.EXIT_OK
    succeeded = capsys.readouterr().out
    assert "massive/reference" in succeeded and "finnhub/earnings" not in succeeded

    assert cli.main(["ingestion", "runs", "--status", "partial"]) == cli.EXIT_OK
    assert "no runs recorded" in capsys.readouterr().out


def test_runs_rejects_a_status_that_is_not_in_the_enum() -> None:
    with pytest.raises(SystemExit) as caught:
        cli.main(["ingestion", "runs", "--status", "done'; DROP TABLE ingestion_run;--"])
    assert caught.value.code == 2


def test_runs_shows_the_id_the_manifest_and_why_a_run_fell_short(
    configured: Path,
    empty_database: None,
    serve: Callable[..., None],
    integration_engine: Engine,
    capsys: pytest.CaptureFixture[str],
) -> None:
    serve(massive=_reference_handler)
    assert cli.main(["ingestion", "reference", "AAPL", "ZZZZ"]) == cli.EXIT_OK  # ZZZZ: unknown
    serve(finnhub=_finnhub(UNDECODABLE))
    assert cli.main(ARGV) == cli.EXIT_RUN_FAILED
    capsys.readouterr()

    assert cli.main(["ingestion", "runs"]) == cli.EXIT_OK

    out = capsys.readouterr().out
    for run in _runs(integration_engine, "earnings") + _runs(integration_engine, "reference"):
        assert f"run: {run.id}" in out
        assert f"raw manifest: {run.raw_artifact_path}" in out
    failed = _runs(integration_engine, "earnings")[0]
    assert failed.error_message and f"error: {failed.error_message}" in out
    assert FINNHUB_KEY not in out and KEY not in out


def test_a_run_that_kept_no_evidence_shows_none(
    configured: Path,
    empty_database: None,
    serve: Callable[..., None],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _with_apple(serve)
    serve(finnhub=_finnhub(b"{}", status=500))
    assert cli.main(ARGV) == cli.EXIT_RUN_FAILED
    capsys.readouterr()

    assert cli.main(["ingestion", "runs", "--dataset", "earnings"]) == cli.EXIT_OK
    assert "raw manifest: none" in capsys.readouterr().out
