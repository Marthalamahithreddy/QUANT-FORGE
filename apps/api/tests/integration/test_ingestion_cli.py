"""End-to-end tests for the ``quantforge ingestion`` command.

These run the real entry point -- argument parsing, the service, the database, the
raw store and the run table -- against a mocked HTTP transport. The mock replaces
only the network, because that is the one part of the path that is neither ours
nor deterministic. A test that stubbed the service too would pass while the wiring
between them was broken, which is exactly the wiring an operator depends on.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from tests.fsds_fixture import (
    ACC_10K,
    ACC_10Q,
    ACC_10QA,
    CIK_MAIN,
    build_package,
    default_nums,
    num_row,
)
from tests.sec_history_fixture import (
    CIK as HISTORY_CIK,
)
from tests.sec_history_fixture import (
    H1_8K,
    H1_10K,
    H1_10Q_A,
    H1_FORM4,
    H2_10K,
    H2_10Q,
    HISTORY_1,
    HISTORY_2,
    R_10K_A,
    R_10Q,
    R_FORM4,
    R_OVERLAP,
    Recorder,
    encode,
    file_entry,
    history,
    parent,
)

from quantforge.core.config import get_settings
from quantforge.ingestion import cli
from quantforge.ingestion.raw_store import RawResponseStore
from quantforge.ingestion.runs import FAILED_RUN_RECORDED
from quantforge.ingestion.service import IngestionService, UnknownListingError
from quantforge.ingestion.sources import get_or_create_data_source
from quantforge.models.benchmark import Benchmark, BenchmarkPriceBar
from quantforge.models.company import Company
from quantforge.models.enums import IngestionStatus, PriceBasis, ProviderType, SecFormType
from quantforge.models.filing import SecFiling
from quantforge.models.financial_fact import FinancialFact, FinancialFactArtifact
from quantforge.models.ingestion import IngestionRun
from quantforge.models.market import DailyPriceBar
from quantforge.models.provenance import DataSource
from quantforge.models.security import Listing, Security
from quantforge.providers.errors import ProviderConfigurationError
from quantforge.providers.massive.client import MassiveClient, MassiveConfig
from quantforge.providers.sec.client import SecClient

pytestmark = pytest.mark.integration

TICKER_PAYLOAD: dict[str, Any] = {
    "ticker": "AAPL",
    "name": "Apple Inc.",
    "cik": "0000320193",
    "figi": "BBG001S5QY8",
    "composite_figi": "BBG000B9XRY4",
    "share_class_figi": "BBG001S5QY8",
    "market": "stocks",
    "locale": "us",
    "primary_exchange": "XNAS",
    "type": "CS",
    "active": True,
    "currency_name": "usd",
}

BARS_PAYLOAD: dict[str, Any] = {
    "ticker": "AAPL",
    "results": [
        {
            "o": 100.0,
            "h": 105.0,
            "l": 99.0,
            "c": 104.0,
            "v": 1_000_000,
            "t": 1_757_000_000_000,
        }
    ],
    "status": "OK",
    "request_id": "abc123",
}

#: Shaped exactly like a real Massive/Polygon aggregates response, including
#: the ``adjusted`` field the provider echoes back -- see
#: ``quantforge.ingestion.benchmark`` on why that field means split-adjusted,
#: not total-return.
SPY_BARS_PAYLOAD: dict[str, Any] = {
    "ticker": "SPY",
    "adjusted": True,
    "results": [
        {
            "o": 680.0,
            "h": 690.0,
            "l": 679.0,
            "c": 685.0,
            "v": 70_000_000,
            "t": 1_767_330_000_000,
        }
    ],
    "status": "OK",
    "request_id": "spy123",
}


@pytest.fixture
def empty_database(integration_engine: Engine) -> Iterator[None]:
    """Guarantee the CLI starts from a known-empty database.

    The command commits for real -- that is its job -- so unlike every other test
    here its writes are not contained by a savepoint and would leak into
    subsequent tests. The database is a throwaway created by the session fixture,
    so truncating is both safe and cheaper than unwinding rows by hand.
    """
    names = ", ".join(sa.inspect(integration_engine).get_table_names())
    with integration_engine.begin() as connection:
        connection.execute(sa.text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
    yield
    with integration_engine.begin() as connection:
        connection.execute(sa.text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))


@pytest.fixture
def configured(
    monkeypatch: pytest.MonkeyPatch,
    integration_engine: Engine,
    tmp_path: Path,
) -> Iterator[Path]:
    """Point the process at the test database and a throwaway raw store."""
    raw_root = tmp_path / "raw"
    # `str(url)` masks the password as "***", which then fails authentication.
    monkeypatch.setenv(
        "QF_DATABASE_URL", integration_engine.url.render_as_string(hide_password=False)
    )
    monkeypatch.setenv("QF_MASSIVE_API_KEY", "test-key-not-real")
    monkeypatch.setenv("QF_INGESTION_RAW_ROOT", str(raw_root))
    # get_settings is lru_cached, so a stale instance would silently point the
    # command at the developer's own database and raw store.
    get_settings.cache_clear()
    try:
        yield raw_root
    finally:
        get_settings.cache_clear()


def _serve(handler: Any) -> Any:
    """Patch the command's client factory to inject a mock transport.

    Wrapped rather than replaced outright so the real ``_client`` still runs: the
    config wiring, the credential check and the base URL all stay in the tested
    path, and only the socket is faked.
    """
    real = cli._client

    def factory(settings: Any) -> MassiveClient:
        client = real(settings)
        config = client._config
        return MassiveClient(config, transport=httpx.MockTransport(handler))

    monkey = pytest.MonkeyPatch()
    monkey.setattr(cli, "_client", factory)
    try:
        yield monkey
    finally:
        monkey.undo()


@pytest.fixture
def serve(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    real = cli._client

    def install(handler: Any) -> None:
        def factory(settings: Any) -> MassiveClient:
            client = real(settings)
            return MassiveClient(client._config, transport=httpx.MockTransport(handler))

        monkeypatch.setattr(cli, "_client", factory)

    yield install
    monkeypatch.undo()


def _ticker_handler(request: httpx.Request) -> httpx.Response:
    """Serve ticker-details and daily-bars responses under their real shapes.

    The adapter decodes the whole document and reads ``results`` itself, so the
    mock must send the real response shape or the validators never run and the
    run fails downstream. A bars request also carries ``ticker`` as a query
    parameter, which is why this dispatches on the path rather than the symbol
    alone.
    """
    if request.url.path.startswith("/v2/aggs/"):
        symbol = request.url.params.get("ticker", "")
        if symbol == "AAPL":
            return httpx.Response(200, json=BARS_PAYLOAD)
        return httpx.Response(404, json={"status": "ERROR", "error": f"unknown ticker {symbol}"})

    symbol = request.url.params.get("ticker", "")
    if symbol == "AAPL":
        return httpx.Response(200, json={"status": "OK", "results": TICKER_PAYLOAD})
    if symbol == "MSFT":
        payload = dict(
            TICKER_PAYLOAD,
            ticker="MSFT",
            name="Microsoft Corporation",
            cik="0000789019",
            figi="BBG001S5QY9",
            share_class_figi="BBG001S5QY9",
        )
        return httpx.Response(200, json={"status": "OK", "results": payload})
    return httpx.Response(404, json={"status": "ERROR", "error": f"unknown ticker {symbol}"})


def _benchmark_handler(request: httpx.Request) -> httpx.Response:
    """Serve a daily-aggregate response for SPY under its real shape.

    Benchmark ingestion uses the identical ``/v2/aggs/`` path as stock daily
    bars -- this handler exists only because ``_ticker_handler`` above always
    answers that path with the AAPL-shaped ``BARS_PAYLOAD``, which would make
    a SPY request resolve as "unknown ticker".
    """
    if request.url.path.startswith("/v2/aggs/"):
        symbol = request.url.params.get("ticker", "")
        if symbol == "SPY":
            return httpx.Response(200, json=SPY_BARS_PAYLOAD)
        return httpx.Response(404, json={"status": "ERROR", "error": f"unknown ticker {symbol}"})
    return httpx.Response(
        404, json={"status": "ERROR", "error": "unexpected path for this handler"}
    )


# ── argument parsing ────────────────────────────────────────────────────────


def test_help_needs_no_configuration(capsys: pytest.CaptureFixture[str]) -> None:
    """A new user's first command must not fail on missing configuration."""
    with pytest.raises(SystemExit) as caught:
        cli.main(["--help"])
    assert caught.value.code == 0
    assert "ingestion" in capsys.readouterr().out


def test_the_ingestion_group_is_required() -> None:
    with pytest.raises(SystemExit) as caught:
        cli.main([])
    assert caught.value.code != 0


@pytest.mark.parametrize(
    "argv",
    [
        ["ingestion", "reference", "AAPL"],
        ["ingestion", "daily", "AAPL", "--start", "2026-09-01", "--end", "2026-09-05"],
        ["ingestion", "runs"],
    ],
)
def test_each_command_is_reachable(argv: list[str]) -> None:
    """The command surface an operator is told about must actually exist."""
    args = cli.build_parser().parse_args(argv)
    assert callable(args.handler)


def test_symbols_are_upper_cased_by_the_parser() -> None:
    args = cli.build_parser().parse_args(["ingestion", "reference", "aapl", "msft"])
    assert args.tickers == ["AAPL", "MSFT"]


def test_a_lowercase_or_blank_symbol_is_rejected() -> None:
    for bad in ("", "   "):
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(["ingestion", "reference", bad])


def test_a_malformed_date_is_rejected() -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(
            ["ingestion", "daily", "AAPL", "--start", "01-09-2026", "--end", "2026-09-05"]
        )


def test_benchmark_requires_an_explicit_name() -> None:
    """Benchmark identity is never inferred from the symbol -- ``--name`` is required."""
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(
            ["ingestion", "benchmark", "SPY", "--start", "2026-09-01", "--end", "2026-09-05"]
        )


def test_an_inverted_range_is_a_usage_error(
    configured: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(["ingestion", "daily", "AAPL", "--start", "2026-09-05", "--end", "2026-09-01"])
    assert code == cli.EXIT_USAGE
    assert "before --start" in capsys.readouterr().err


def test_a_non_positive_limit_is_a_usage_error(configured: Path) -> None:
    assert cli.main(["ingestion", "runs", "--limit", "0"]) == cli.EXIT_USAGE


def test_a_missing_key_is_a_usage_error_not_a_failed_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Nothing was ingested, so the invocation is what is wrong.

    Reporting this as a failed run invites a retry loop that cannot succeed.
    """
    monkeypatch.delenv("QF_MASSIVE_API_KEY", raising=False)
    get_settings.cache_clear()
    try:
        code = cli.main(["ingestion", "reference", "AAPL"])
    finally:
        get_settings.cache_clear()
    assert code == cli.EXIT_USAGE
    assert "QF_MASSIVE_API_KEY" in capsys.readouterr().err


def test_the_client_factory_refuses_to_build_without_a_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("QF_MASSIVE_API_KEY", raising=False)
    get_settings.cache_clear()
    try:
        with pytest.raises(ProviderConfigurationError, match="QF_MASSIVE_API_KEY"):
            cli._client(get_settings())
    finally:
        get_settings.cache_clear()


def test_the_client_factory_carries_the_configured_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QF_MASSIVE_API_KEY", "k")
    monkeypatch.setenv("QF_MASSIVE_BASE_URL", "https://example.test/v2")
    get_settings.cache_clear()
    try:
        config: MassiveConfig = cli._client(get_settings())._config
        assert config.base_url == "https://example.test/v2"
        assert config.api_key is not None
        assert config.api_key.get_secret_value() == "k"
    finally:
        get_settings.cache_clear()


def test_the_client_factory_carries_the_configured_pacing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``QF_MASSIVE_RETRY_BASE_DELAY_SECONDS``/``..._MIN_REQUEST_INTERVAL_SECONDS``
    must reach the constructed client's actual rate limiter and backoff, not
    just the config object that briefly holds them on the way there.
    """
    monkeypatch.setenv("QF_MASSIVE_API_KEY", "k")
    monkeypatch.setenv("QF_MASSIVE_RETRY_BASE_DELAY_SECONDS", "2.5")
    monkeypatch.setenv("QF_MASSIVE_MIN_REQUEST_INTERVAL_SECONDS", "1.5")
    get_settings.cache_clear()
    try:
        client = cli._client(get_settings())
        assert client._config.retry_base_delay == 2.5
        assert client._config.min_request_interval == 1.5
        assert client._backoff.base_delay == 2.5
        assert client._limiter._min_interval == 1.5
    finally:
        get_settings.cache_clear()


# ── reference ingestion ─────────────────────────────────────────────────────


def test_reference_stores_the_company_and_reports_success(
    configured: Path,
    empty_database: None,
    serve: Any,
    integration_engine: Engine,
    capsys: pytest.CaptureFixture[str],
) -> None:
    serve(_ticker_handler)

    code = cli.main(["ingestion", "reference", "AAPL"])
    out = capsys.readouterr().out

    assert code == cli.EXIT_OK
    assert "AAPL" in out
    with Session(integration_engine) as session:
        company = session.query(Company).one()
        assert company.display_name == "Apple Inc."
        assert company.cik == "0000320193"
        assert session.query(Security).count() == 1
        assert session.query(Listing).count() == 1


def test_reference_preserves_the_raw_response(
    configured: Path, empty_database: None, serve: Any, integration_engine: Engine
) -> None:
    """The stored bytes are the provider's, not ours.

    Re-serialising would normalise key order and drop the original's float
    spelling, and then a later re-fetch could not be compared against what we
    actually received.
    """
    serve(_ticker_handler)

    cli.main(["ingestion", "reference", "AAPL"])

    stored = list(configured.rglob("*.json"))
    assert stored, "the raw response should have been preserved"
    body = next(p for p in stored if not p.name.endswith(".meta.json"))
    assert json.loads(body.read_text()) == {"status": "OK", "results": TICKER_PAYLOAD}
    assert (body.parent / f"{body.stem}.meta.json").exists()

    with Session(integration_engine) as session:
        run = session.query(IngestionRun).one()
        assert run.raw_content_sha256 is not None
        assert run.raw_artifact_path is not None


def test_no_credential_is_written_to_disk(
    configured: Path, empty_database: None, serve: Any
) -> None:
    serve(_ticker_handler)
    cli.main(["ingestion", "reference", "AAPL"])

    assert list(configured.rglob("*"))
    for path in configured.rglob("*"):
        if path.is_file():
            assert "test-key-not-real" not in path.read_text()


def test_no_credential_reaches_the_database(
    configured: Path, empty_database: None, serve: Any, integration_engine: Engine
) -> None:
    """Request parameters are persisted, so they are the likeliest leak point."""
    serve(_ticker_handler)
    cli.main(["ingestion", "reference", "AAPL"])

    with Session(integration_engine) as session:
        run = session.query(IngestionRun).one()
        assert "test-key-not-real" not in json.dumps(run.request_parameters)
        assert run.error_message is None or "test-key-not-real" not in run.error_message


def test_several_symbols_are_ingested_in_one_run(
    configured: Path, empty_database: None, serve: Any, integration_engine: Engine
) -> None:
    serve(_ticker_handler)

    code = cli.main(["ingestion", "reference", "AAPL", "MSFT"])

    assert code == cli.EXIT_OK
    with Session(integration_engine) as session:
        assert session.query(Company).count() == 2
        assert session.query(IngestionRun).count() == 1


def test_re_running_reference_is_idempotent(
    configured: Path, empty_database: None, serve: Any, integration_engine: Engine
) -> None:
    """Running the same command twice must not double the data."""
    serve(_ticker_handler)

    cli.main(["ingestion", "reference", "AAPL"])
    cli.main(["ingestion", "reference", "AAPL"])

    with Session(integration_engine) as session:
        assert session.query(Company).count() == 1
        assert session.query(Security).count() == 1
        assert session.query(Listing).count() == 1
        # Two runs, though: the second one is evidence the repeat was a no-op.
        assert session.query(IngestionRun).count() == 2
        newest = session.query(IngestionRun).order_by(IngestionRun.started_at.desc()).first()
        assert newest is not None
        assert newest.records_written == 0


def test_an_unknown_symbol_writes_nothing(
    configured: Path, empty_database: None, serve: Any, integration_engine: Engine
) -> None:
    """A provider-unknown ticker is an answer, not an error.

    The batch still succeeds -- "no such symbol" is legitimate information
    useful to the operator -- but nothing is written: no issuer rows, and the
    run reports the ticker as skipped rather than resolved.
    """
    serve(_ticker_handler)

    code = cli.main(["ingestion", "reference", "NOSUCH"])

    assert code == cli.EXIT_OK
    with Session(integration_engine) as session:
        assert session.query(Company).count() == 0
        run = session.query(IngestionRun).one()
        assert run.status is IngestionStatus.SUCCEEDED
        assert run.records_received == 1
        assert run.records_written == 0
        assert run.records_skipped == 1


# ── daily ingestion ─────────────────────────────────────────────────────────


def test_daily_requires_the_symbol_to_be_ingested_first(
    configured: Path,
    empty_database: None,
    serve: Any,
    integration_engine: Engine,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A bar's session date depends on its venue, so the listing must exist.

    Guessing the venue would put every session on the wrong calendar.
    """
    serve(_ticker_handler)
    code = cli.main(["ingestion", "daily", "AAPL", "--start", "2026-09-01", "--end", "2026-09-05"])

    assert code == cli.EXIT_USAGE
    assert "AAPL" in capsys.readouterr().err
    with Session(integration_engine) as session:
        assert session.query(DailyPriceBar).count() == 0


def test_daily_stores_bars_and_links_them_to_the_run(
    configured: Path,
    empty_database: None,
    serve: Any,
    integration_engine: Engine,
    capsys: pytest.CaptureFixture[str],
) -> None:
    serve(_ticker_handler)
    assert cli.main(["ingestion", "reference", "AAPL"]) == cli.EXIT_OK
    capsys.readouterr()

    code = cli.main(["ingestion", "daily", "AAPL", "--start", "2026-09-01", "--end", "2026-09-05"])

    assert code == cli.EXIT_OK
    assert "written=1" in capsys.readouterr().out
    with Session(integration_engine) as session:
        bar = session.query(DailyPriceBar).one()
        run = session.query(IngestionRun).filter_by(dataset="daily-bars").one()
        assert bar.ingestion_run_id == run.id, "the row must name its origin"
        assert run.status is IngestionStatus.SUCCEEDED
        assert run.records_written == 1


def test_daily_is_idempotent_across_runs(
    configured: Path, empty_database: None, serve: Any, integration_engine: Engine
) -> None:
    serve(_ticker_handler)
    cli.main(["ingestion", "reference", "AAPL"])
    argv = ["ingestion", "daily", "AAPL", "--start", "2026-09-01", "--end", "2026-09-05"]

    assert cli.main(argv) == cli.EXIT_OK
    assert cli.main(argv) == cli.EXIT_OK

    with Session(integration_engine) as session:
        assert session.query(DailyPriceBar).count() == 1
        daily_runs = (
            session.query(IngestionRun)
            .filter_by(dataset="daily-bars")
            .order_by(IngestionRun.started_at.desc())
            .all()
        )
        assert daily_runs[0].records_written == 0
        assert daily_runs[0].records_skipped == 1


# ── failed runs are durable; their domain writes are not ────────────────────

_EARLIER_BAR_T = 1_756_827_200_000  # two days before BARS_PAYLOAD's bar


def _bars_handler_with(results: list[dict[str, Any]]) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/v2/aggs/"):
            return httpx.Response(200, json=dict(BARS_PAYLOAD, results=results))
        return _ticker_handler(request)

    return handler


def test_a_failed_daily_run_is_recorded_but_writes_no_domain_rows(
    configured: Path,
    empty_database: None,
    serve: Any,
    integration_engine: Engine,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A conflict after one valid new bar: the run survives, the new bar does not."""
    serve(_ticker_handler)
    assert cli.main(["ingestion", "reference", "AAPL"]) == cli.EXIT_OK
    argv = ["ingestion", "daily", "AAPL", "--start", "2026-09-01", "--end", "2026-09-05"]
    assert cli.main(argv) == cli.EXIT_OK
    capsys.readouterr()

    original = BARS_PAYLOAD["results"][0]
    earlier_new_bar = dict(original, t=_EARLIER_BAR_T)
    revised = dict(original, c=101.0)  # same session, different close (within H/L) -> conflict
    serve(_bars_handler_with([earlier_new_bar, revised]))
    assert cli.main(argv) == cli.EXIT_RUN_FAILED

    with Session(integration_engine) as session:
        bars = session.query(DailyPriceBar).all()
        assert len(bars) == 1, "the earlier, valid bar must roll back with the failed run"
        assert bars[0].close == Decimal("104.000000"), "nothing was overwritten"
        failed = session.query(IngestionRun).filter_by(status=IngestionStatus.FAILED).one()
        assert failed.dataset == "daily-bars"
        assert failed.error_type == "ProviderDataConflict"
        assert failed.records_written == 0
        assert failed.completed_at is not None
        assert failed.raw_artifact_path, "the fetched evidence must stay referenced"
        succeeded = session.query(IngestionRun).filter_by(status=IngestionStatus.SUCCEEDED).all()
        assert {run.dataset for run in succeeded} == {"reference", "daily-bars"}


def test_a_provider_failure_before_any_write_still_leaves_a_failed_run(
    configured: Path,
    empty_database: None,
    serve: Any,
    integration_engine: Engine,
) -> None:
    serve(_ticker_handler)
    assert cli.main(["ingestion", "reference", "MSFT"]) == cli.EXIT_OK

    # The stub answers bars only for AAPL, so MSFT's bar request is a provider 404.
    code = cli.main(["ingestion", "daily", "MSFT", "--start", "2026-09-01", "--end", "2026-09-05"])

    assert code == cli.EXIT_RUN_FAILED
    with Session(integration_engine) as session:
        failed = session.query(IngestionRun).filter_by(status=IngestionStatus.FAILED).one()
        assert failed.dataset == "daily-bars"
        assert failed.error_type
        assert session.query(DailyPriceBar).count() == 0


def test_a_failed_first_benchmark_run_rolls_back_identity_and_source_but_keeps_the_run(
    configured: Path,
    empty_database: None,
    serve: Any,
    integration_engine: Engine,
) -> None:
    serve(_ticker_handler)  # answers SPY's bar request with a provider 404

    code = cli.main(
        [
            "ingestion",
            "benchmark",
            "SPY",
            "--start",
            "2026-01-02",
            "--end",
            "2026-01-09",
            "--name",
            "SPY (ETF proxy for the S&P 500)",
        ]
    )

    assert code == cli.EXIT_RUN_FAILED
    with Session(integration_engine) as session:
        assert session.query(Benchmark).count() == 0, "no partial identity row"
        assert session.query(BenchmarkPriceBar).count() == 0
        assert session.query(DataSource).count() == 0, "created inside the failed block"
        failed = session.query(IngestionRun).filter_by(status=IngestionStatus.FAILED).one()
        assert failed.dataset == "benchmark-daily-bars"


def test_a_failed_benchmark_conflict_keeps_the_run_and_the_original_levels(
    configured: Path,
    empty_database: None,
    serve: Any,
    integration_engine: Engine,
) -> None:
    serve(_benchmark_handler)
    argv = [
        "ingestion",
        "benchmark",
        "SPY",
        "--start",
        "2026-01-02",
        "--end",
        "2026-01-09",
        "--name",
        "SPY (ETF proxy for the S&P 500)",
    ]
    assert cli.main(argv) == cli.EXIT_OK

    original = SPY_BARS_PAYLOAD["results"][0]
    revised = dict(original, c=689.0)  # within the bar's high/low, but a different close

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=dict(SPY_BARS_PAYLOAD, results=[revised]))

    serve(handler)
    assert cli.main(argv) == cli.EXIT_RUN_FAILED

    with Session(integration_engine) as session:
        level = session.query(BenchmarkPriceBar).one()
        assert level.close == Decimal("685.000000")
        failed = session.query(IngestionRun).filter_by(status=IngestionStatus.FAILED).one()
        assert failed.error_type == "BenchmarkBarConflictError"


def test_a_successful_run_still_commits_exactly_as_before(
    configured: Path,
    empty_database: None,
    serve: Any,
    integration_engine: Engine,
) -> None:
    serve(_ticker_handler)
    assert cli.main(["ingestion", "reference", "AAPL"]) == cli.EXIT_OK
    assert (
        cli.main(["ingestion", "daily", "AAPL", "--start", "2026-09-01", "--end", "2026-09-05"])
        == cli.EXIT_OK
    )

    with Session(integration_engine) as session:
        assert session.query(IngestionRun).filter_by(status=IngestionStatus.FAILED).count() == 0
        assert session.query(DailyPriceBar).count() == 1
        assert session.query(DataSource).count() == 1


# ── price basis end to end ──────────────────────────────────────────────────


def _basis_aware_handler(
    seen: list[str], *, bars: dict[str, Any], scale: int = 10, lie_about_adjusted: bool = False
) -> Any:
    """Serve reference data, and bars whose values and ``adjusted`` echo follow the request.

    ``adjusted=false`` returns prices ``scale`` times larger (a split-affected session);
    ``lie_about_adjusted`` makes the response echo the opposite of what was asked.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if not request.url.path.startswith("/v2/aggs/"):
            return _ticker_handler(request)
        adjusted = request.url.params["adjusted"]
        seen.append(adjusted)
        factor = 1 if adjusted == "true" else scale
        row = bars["results"][0]
        scaled = dict(row, **{key: row[key] * factor for key in ("o", "h", "l", "c")})
        echoed = (adjusted == "true") != lie_about_adjusted
        return httpx.Response(200, json=dict(bars, results=[scaled], adjusted=echoed))

    return handler


_DAILY_ARGV = ["ingestion", "daily", "AAPL", "--start", "2026-09-01", "--end", "2026-09-05"]


def test_daily_defaults_to_the_split_adjusted_research_basis(
    configured: Path, empty_database: None, serve: Any, integration_engine: Engine
) -> None:
    seen: list[str] = []
    serve(_basis_aware_handler(seen, bars=BARS_PAYLOAD))
    assert cli.main(["ingestion", "reference", "AAPL"]) == cli.EXIT_OK

    assert cli.main(_DAILY_ARGV) == cli.EXIT_OK

    assert seen == ["true"]
    with Session(integration_engine) as session:
        bar = session.query(DailyPriceBar).one()
        assert bar.price_basis is PriceBasis.SPLIT_ADJUSTED
        run = session.query(IngestionRun).filter_by(dataset="daily-bars").one()
        assert run.request_parameters["price_basis"] == "SPLIT_ADJUSTED"


def test_daily_as_traded_requests_adjusted_false_and_stores_a_separate_row(
    configured: Path, empty_database: None, serve: Any, integration_engine: Engine
) -> None:
    seen: list[str] = []
    serve(_basis_aware_handler(seen, bars=BARS_PAYLOAD))
    assert cli.main(["ingestion", "reference", "AAPL"]) == cli.EXIT_OK
    assert cli.main(_DAILY_ARGV) == cli.EXIT_OK

    assert cli.main([*_DAILY_ARGV, "--price-basis", "as-traded"]) == cli.EXIT_OK

    assert seen == ["true", "false"]
    with Session(integration_engine) as session:
        rows = {bar.price_basis: bar for bar in session.query(DailyPriceBar).all()}
        assert rows[PriceBasis.SPLIT_ADJUSTED].close == Decimal("104.000000")
        assert rows[PriceBasis.AS_TRADED].close == Decimal("1040.000000")
        assert session.query(IngestionRun).filter_by(status=IngestionStatus.FAILED).count() == 0
        traded_run = (
            session.query(IngestionRun)
            .filter_by(dataset="daily-bars")
            .order_by(IngestionRun.started_at.desc())
            .first()
        )
        assert traded_run is not None
        assert traded_run.request_parameters["price_basis"] == "AS_TRADED"


def test_each_basis_is_idempotent_on_rerun(
    configured: Path, empty_database: None, serve: Any, integration_engine: Engine
) -> None:
    seen: list[str] = []
    serve(_basis_aware_handler(seen, bars=BARS_PAYLOAD))
    assert cli.main(["ingestion", "reference", "AAPL"]) == cli.EXIT_OK
    for _ in range(2):
        assert cli.main(_DAILY_ARGV) == cli.EXIT_OK
        assert cli.main([*_DAILY_ARGV, "--price-basis", "as-traded"]) == cli.EXIT_OK

    with Session(integration_engine) as session:
        assert session.query(DailyPriceBar).count() == 2


def test_a_response_that_contradicts_the_requested_basis_stores_nothing(
    configured: Path, empty_database: None, serve: Any, integration_engine: Engine
) -> None:
    serve(_basis_aware_handler([], bars=BARS_PAYLOAD))
    assert cli.main(["ingestion", "reference", "AAPL"]) == cli.EXIT_OK
    serve(_basis_aware_handler([], bars=BARS_PAYLOAD, lie_about_adjusted=True))

    code = cli.main([*_DAILY_ARGV, "--price-basis", "as-traded"])

    assert code == cli.EXIT_RUN_FAILED
    with Session(integration_engine) as session:
        assert session.query(DailyPriceBar).count() == 0
        failed = session.query(IngestionRun).filter_by(status=IngestionStatus.FAILED).one()
        assert "refusing to ingest" in (failed.error_message or "")
        assert failed.raw_artifact_path, "the mismatched response is kept as evidence"
        assert failed.records_written == 0
    manifest = (configured / failed.raw_artifact_path).read_text()
    assert "daily-bars" in manifest
    assert "daily-bars-rejected" in manifest, "stored apart from accepted artifacts"
    artifacts = [p for p in configured.rglob("*.json") if "manifest" not in str(p)]
    assert all("daily-bars-rejected" in str(p) for p in artifacts if "reference" not in str(p))
    mismatched = [
        p
        for p in artifacts
        if isinstance(json.loads(p.read_text()), dict)
        and json.loads(p.read_text()).get("adjusted") is True
    ]
    assert mismatched, "the raw bytes of the contradicting response were stored"
    for path in configured.rglob("*"):
        if path.is_file():
            assert "test-key-not-real" not in path.read_text(), path


def test_an_unknown_price_basis_is_a_usage_error(configured: Path) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main([*_DAILY_ARGV, "--price-basis", "raw"])

    assert exit_info.value.code == cli.EXIT_USAGE


def test_benchmark_stores_both_bases_as_separate_levels(
    configured: Path, empty_database: None, serve: Any, integration_engine: Engine
) -> None:
    seen: list[str] = []
    serve(_basis_aware_handler(seen, bars=SPY_BARS_PAYLOAD))
    argv = [
        "ingestion",
        "benchmark",
        "SPY",
        "--start",
        "2026-01-02",
        "--end",
        "2026-01-09",
        "--name",
        "SPY (ETF proxy for the S&P 500)",
    ]
    # The shared handler answers the ticker path for AAPL/MSFT only; benchmark
    # ingestion needs no reference call, just the aggregates path.
    assert cli.main(argv) == cli.EXIT_OK
    assert cli.main([*argv, "--price-basis", "as-traded"]) == cli.EXIT_OK

    assert seen == ["true", "false"]
    with Session(integration_engine) as session:
        levels = {level.price_basis: level for level in session.query(BenchmarkPriceBar).all()}
        assert levels[PriceBasis.SPLIT_ADJUSTED].close == Decimal("685.000000")
        assert levels[PriceBasis.AS_TRADED].close == Decimal("6850.000000")
        assert session.query(Benchmark).count() == 1


# ── benchmark ingestion ─────────────────────────────────────────────────────

_SPY_NAME = "SPY (ETF proxy for the S&P 500)"


def test_benchmark_stores_levels_and_links_them_to_the_run(
    configured: Path,
    empty_database: None,
    serve: Any,
    integration_engine: Engine,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No reference ingestion is needed first -- a benchmark has no Listing to resolve."""
    serve(_benchmark_handler)

    code = cli.main(
        [
            "ingestion",
            "benchmark",
            "SPY",
            "--start",
            "2026-01-02",
            "--end",
            "2026-01-09",
            "--name",
            _SPY_NAME,
        ]
    )

    assert code == cli.EXIT_OK
    assert "written=1" in capsys.readouterr().out
    with Session(integration_engine) as session:
        benchmark = session.query(Benchmark).one()
        assert benchmark.symbol == "SPY"
        assert benchmark.name == _SPY_NAME
        level = session.query(BenchmarkPriceBar).one()
        assert level.close == Decimal("685.000000")
        assert level.benchmark_id == benchmark.id
        # BenchmarkPriceBar has no ingestion_run_id column (unlike DailyPriceBar),
        # so the link back to "which run wrote this" is by dataset/source/time
        # rather than a foreign key -- a known, pre-existing schema asymmetry,
        # not something this step introduces or can fix without a migration.
        run = session.query(IngestionRun).filter_by(dataset="benchmark-daily-bars").one()
        assert run.status is IngestionStatus.SUCCEEDED
        assert run.records_written == 1


def test_benchmark_is_idempotent_across_runs(
    configured: Path, empty_database: None, serve: Any, integration_engine: Engine
) -> None:
    serve(_benchmark_handler)
    argv = [
        "ingestion",
        "benchmark",
        "SPY",
        "--start",
        "2026-01-02",
        "--end",
        "2026-01-09",
        "--name",
        _SPY_NAME,
    ]

    assert cli.main(argv) == cli.EXIT_OK
    assert cli.main(argv) == cli.EXIT_OK

    with Session(integration_engine) as session:
        assert session.query(Benchmark).count() == 1
        assert session.query(BenchmarkPriceBar).count() == 1
        benchmark_runs = (
            session.query(IngestionRun)
            .filter_by(dataset="benchmark-daily-bars")
            .order_by(IngestionRun.started_at.desc())
            .all()
        )
        assert benchmark_runs[0].records_written == 0
        assert benchmark_runs[0].records_skipped == 1


def test_benchmark_rejects_an_incompatible_existing_identity(
    configured: Path,
    empty_database: None,
    serve: Any,
    integration_engine: Engine,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A different --name for an already-used symbol is a conflict, not a silent rename."""
    serve(_benchmark_handler)
    base_argv = ["ingestion", "benchmark", "SPY", "--start", "2026-01-02", "--end", "2026-01-09"]
    assert cli.main([*base_argv, "--name", _SPY_NAME]) == cli.EXIT_OK

    code = cli.main([*base_argv, "--name", "Something Else Entirely"])

    assert code == cli.EXIT_USAGE
    assert "SPY" in capsys.readouterr().err
    with Session(integration_engine) as session:
        assert session.query(Benchmark).count() == 1
        assert session.query(Benchmark).one().name == _SPY_NAME


def test_benchmark_does_not_leak_the_credential(
    configured: Path, empty_database: None, serve: Any, integration_engine: Engine
) -> None:
    serve(_benchmark_handler)
    cli.main(
        [
            "ingestion",
            "benchmark",
            "SPY",
            "--start",
            "2026-01-02",
            "--end",
            "2026-01-09",
            "--name",
            _SPY_NAME,
        ]
    )

    assert list(configured.rglob("*"))
    for path in configured.rglob("*"):
        if path.is_file():
            assert "test-key-not-real" not in path.read_text()
    with Session(integration_engine) as session:
        run = session.query(IngestionRun).filter_by(dataset="benchmark-daily-bars").one()
        assert "test-key-not-real" not in json.dumps(run.request_parameters)


# ── runs listing ────────────────────────────────────────────────────────────


def test_runs_reports_nothing_when_empty(
    configured: Path, empty_database: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["ingestion", "runs"]) == cli.EXIT_OK
    assert "no runs recorded" in capsys.readouterr().out


def test_runs_lists_what_ran(
    configured: Path, empty_database: None, serve: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    serve(_ticker_handler)
    cli.main(["ingestion", "reference", "AAPL"])
    capsys.readouterr()

    assert cli.main(["ingestion", "runs"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "massive" in out
    assert "reference" in out
    assert "succeeded" in out


def test_runs_can_filter_by_dataset(
    configured: Path,
    empty_database: None,
    serve: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    serve(_ticker_handler)
    cli.main(["ingestion", "reference", "AAPL"])
    capsys.readouterr()

    assert cli.main(["ingestion", "runs", "--dataset", "daily-bars"]) == cli.EXIT_OK
    assert "no runs recorded" in capsys.readouterr().out


def test_runs_does_not_leak_the_credential(
    configured: Path, empty_database: None, serve: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    serve(_ticker_handler)
    cli.main(["ingestion", "reference", "AAPL"])
    capsys.readouterr()

    cli.main(["ingestion", "runs"])
    assert "test-key-not-real" not in capsys.readouterr().out


def test_a_stale_failed_run_marker_never_authorises_a_commit_after_a_pre_run_failure(
    integration_engine: Engine, tmp_path: Path
) -> None:
    """A reused session's marker from an earlier failed run is cleared by the next attempt,
    so a failure *before* any run exists cannot commit the caller's pending state."""

    class NoProvider:
        name = "massive"

    now = datetime(2026, 9, 27, tzinfo=UTC)
    with Session(integration_engine) as session:
        session.info[FAILED_RUN_RECORDED] = True  # left over from an earlier failed run
        service = IngestionService(
            session=session,
            provider=NoProvider(),  # type: ignore[arg-type]
            store=RawResponseStore(root=tmp_path / "raw"),
            now=now,
        )
        with pytest.raises(UnknownListingError):
            asyncio.run(
                service.ingest_daily_bars(
                    "NOPE",
                    date(2026, 9, 1),
                    date(2026, 9, 2),
                    price_basis=PriceBasis.SPLIT_ADJUSTED,
                )
            )

        assert FAILED_RUN_RECORDED not in session.info
        cli._commit_failed_run(session)  # no marker -> rolls back rather than committing
        assert session.scalar(sa.select(sa.func.count()).select_from(IngestionRun)) == 0


def test_a_raw_store_failure_while_keeping_evidence_never_masks_the_mismatch(
    configured: Path,
    empty_database: None,
    serve: Any,
    integration_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    serve(_basis_aware_handler([], bars=BARS_PAYLOAD))
    assert cli.main(["ingestion", "reference", "AAPL"]) == cli.EXIT_OK
    serve(_basis_aware_handler([], bars=BARS_PAYLOAD, lie_about_adjusted=True))
    real = RawResponseStore.store_all

    def broken(self: RawResponseStore, captures: Any) -> Any:
        if captures and captures[0].dataset.endswith("-rejected"):
            raise OSError("disk full")
        return real(self, captures)

    monkeypatch.setattr(RawResponseStore, "store_all", broken)

    code = cli.main([*_DAILY_ARGV, "--price-basis", "as-traded"])

    assert code == cli.EXIT_RUN_FAILED, "still the clean provider failure, not a traceback"
    assert "refusing to ingest" in capsys.readouterr().err
    with Session(integration_engine) as session:
        failed = session.query(IngestionRun).filter_by(status=IngestionStatus.FAILED).one()
        assert "refusing to ingest" in (failed.error_message or "")
        assert session.query(DailyPriceBar).count() == 0


# ── SEC filing metadata (no network: a mock transport stands in for data.sec.gov) ──

_SEC_UA = "QuantForgeCliTest ops@example.com"
_SEC_BODY = json.dumps(
    {
        "cik": "320193",
        "filings": {
            "recent": {
                "accessionNumber": ["0000320193-24-000010", "0000320193-24-000011"],
                "form": ["10-Q", "4"],
                "filingDate": ["2024-05-03", "2024-05-03"],
                "reportDate": ["2024-03-30", ""],
                "acceptanceDateTime": ["2024-05-03T16:30:22.000Z", "2024-05-03T16:31:00.000Z"],
                "primaryDocument": ["q.htm", "x.xml"],
                "items": ["", ""],
            }
        },
    }
).encode()


@pytest.fixture
def sec_transport(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[httpx.Request]]:
    """Make the CLI's SEC client talk to a mock transport, recording the requests."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=_SEC_BODY)

    real = cli._sec_client

    def factory(settings: Any) -> Any:
        client = real(settings)
        return SecClient(client._config, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(cli, "_sec_client", factory)
    yield requests


def _company_with_cik(engine: Engine, cik: str = "0000320193") -> None:
    with Session(engine) as session:
        session.add(Company(legal_name="SEC Probe Inc.", display_name="SEC Probe", cik=cik))
        session.commit()


def test_sec_filings_requires_a_declared_identity(
    configured: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("QF_SEC_USER_AGENT", raising=False)
    get_settings.cache_clear()

    code = cli.main(["ingestion", "sec-filings", "320193"])

    assert code == cli.EXIT_USAGE
    assert "QF_SEC_USER_AGENT" in capsys.readouterr().err


def test_sec_filings_ingests_metadata_and_declares_the_identity(
    configured: Path,
    empty_database: None,
    monkeypatch: pytest.MonkeyPatch,
    sec_transport: list[httpx.Request],
    integration_engine: Engine,
) -> None:
    monkeypatch.setenv("QF_SEC_USER_AGENT", _SEC_UA)
    get_settings.cache_clear()
    _company_with_cik(integration_engine)

    code = cli.main(["ingestion", "sec-filings", "320193"])

    assert code == cli.EXIT_OK
    assert sec_transport[0].headers["User-Agent"] == _SEC_UA
    assert sec_transport[0].url.path == "/submissions/CIK0000320193.json"
    with Session(integration_engine) as session:
        filing = session.query(SecFiling).one()  # Form 4 is not a supported form
        assert filing.accession_number == "0000320193-24-000010"
        run = session.query(IngestionRun).filter_by(dataset="sec-filings").one()
        assert run.status is IngestionStatus.SUCCEEDED
        assert run.provider == "sec-edgar"
        assert "ops@example.com" not in json.dumps(run.request_parameters)
    for path in configured.rglob("*"):
        if path.is_file():
            assert "ops@example.com" not in path.read_text(), "the identity is never stored"
    assert (configured / "sec-edgar").is_dir()


def test_sec_filings_for_an_unknown_cik_is_a_usage_error_with_a_durable_failed_run(
    configured: Path,
    empty_database: None,
    monkeypatch: pytest.MonkeyPatch,
    sec_transport: list[httpx.Request],
    integration_engine: Engine,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("QF_SEC_USER_AGENT", _SEC_UA)
    get_settings.cache_clear()

    code = cli.main(["ingestion", "sec-filings", "320193"])

    assert code == cli.EXIT_USAGE
    assert "no stored company has CIK 0000320193" in capsys.readouterr().err
    assert sec_transport == [], "no request is made for a registrant we do not hold"
    with Session(integration_engine) as session:
        assert session.query(Company).count() == 0
        assert session.query(SecFiling).count() == 0
        assert session.query(IngestionRun).filter_by(status=IngestionStatus.FAILED).count() == 1


def test_a_malformed_cik_argument_is_a_usage_error(
    configured: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("QF_SEC_USER_AGENT", _SEC_UA)
    get_settings.cache_clear()

    assert cli.main(["ingestion", "sec-filings", "not-a-cik"]) == cli.EXIT_USAGE


# ── SEC filing history (identity only; a mock transport stands in for data.sec.gov) ──


@pytest.fixture
def sec_history_transport(monkeypatch: pytest.MonkeyPatch) -> Iterator[Recorder]:
    """Make the CLI's SEC client serve a parent response plus two history files."""
    doc = parent(
        [R_10Q, R_10K_A, R_FORM4, R_OVERLAP],
        [
            file_entry(HISTORY_1, [H1_10K, H1_10Q_A, H1_8K, H1_FORM4]),
            file_entry(HISTORY_2, [H2_10K, H2_10Q]),
        ],
    )
    recorder = Recorder(
        {
            f"/submissions/CIK{HISTORY_CIK}.json": encode(doc),
            f"/submissions/{HISTORY_1}": encode(history([H1_10K, H1_10Q_A, H1_8K, H1_FORM4])),
            f"/submissions/{HISTORY_2}": encode(history([H2_10K, H2_10Q])),
        }
    )
    real = cli._sec_client

    def factory(settings: Any) -> Any:
        return SecClient(real(settings)._config, transport=recorder.transport)

    monkeypatch.setattr(cli, "_sec_client", factory)
    yield recorder


def test_sec_filings_history_stores_older_identity_and_states_its_scope(
    configured: Path,
    empty_database: None,
    monkeypatch: pytest.MonkeyPatch,
    sec_history_transport: Recorder,
    integration_engine: Engine,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("QF_SEC_USER_AGENT", _SEC_UA)
    get_settings.cache_clear()
    _company_with_cik(integration_engine)

    code = cli.main(["ingestion", "sec-filings-history", "320193"])

    out = capsys.readouterr().out
    assert code == cli.EXIT_OK
    assert sec_history_transport.paths == [
        f"/submissions/CIK{HISTORY_CIK}.json",
        f"/submissions/{HISTORY_1}",
        f"/submissions/{HISTORY_2}",
    ]
    assert set(sec_history_transport.user_agents) == {_SEC_UA}
    assert "history files: discovered 2, fetched 2, skipped by the accession scope 0" in out
    assert "does not claim the registrant's history is complete" in out
    with Session(integration_engine) as session:
        accessions = {f.accession_number for f in session.query(SecFiling)}
        assert H2_10K[0] in accessions and R_10Q[0] in accessions
        run = session.query(IngestionRun).filter_by(dataset="sec-filing-history").one()
        assert run.status is IngestionStatus.SUCCEEDED
    for path in configured.rglob("*"):
        if path.is_file():
            assert "ops@example.com" not in path.read_text(errors="ignore")


def test_sec_filings_history_target_scope_skips_unneeded_files(
    configured: Path,
    empty_database: None,
    monkeypatch: pytest.MonkeyPatch,
    sec_history_transport: Recorder,
    integration_engine: Engine,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("QF_SEC_USER_AGENT", _SEC_UA)
    get_settings.cache_clear()
    _company_with_cik(integration_engine)

    code = cli.main(
        [
            "ingestion",
            "sec-filings-history",
            "320193",
            "--accession",
            H2_10K[0],
            "--accession",
            "0000320193-14-999999",
        ]
    )

    out = capsys.readouterr().out
    assert code == cli.EXIT_OK
    assert sec_history_transport.paths[-1] == f"/submissions/{HISTORY_2}"
    assert HISTORY_1 not in " ".join(sec_history_transport.paths)
    assert (
        "unresolved: 0000320193-14-999999 (not found in what was fetched; NOT proof of absence)"
        in out
    )


def test_sec_filings_history_over_the_bound_is_a_usage_error_before_any_history_request(
    configured: Path,
    empty_database: None,
    monkeypatch: pytest.MonkeyPatch,
    sec_history_transport: Recorder,
    integration_engine: Engine,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("QF_SEC_USER_AGENT", _SEC_UA)
    get_settings.cache_clear()
    _company_with_cik(integration_engine)

    code = cli.main(["ingestion", "sec-filings-history", "320193", "--max-history-files", "1"])

    assert code == cli.EXIT_USAGE
    assert "explicit bound" in capsys.readouterr().err
    assert sec_history_transport.paths == [f"/submissions/CIK{HISTORY_CIK}.json"]
    with Session(integration_engine) as session:
        assert session.query(SecFiling).count() == 0
        assert session.query(IngestionRun).filter_by(status=IngestionStatus.FAILED).count() == 1


def test_sec_filings_history_for_an_unknown_cik_makes_no_request(
    configured: Path,
    empty_database: None,
    monkeypatch: pytest.MonkeyPatch,
    sec_history_transport: Recorder,
    integration_engine: Engine,
) -> None:
    monkeypatch.setenv("QF_SEC_USER_AGENT", _SEC_UA)
    get_settings.cache_clear()

    assert cli.main(["ingestion", "sec-filings-history", "320193"]) == cli.EXIT_USAGE
    assert sec_history_transport.paths == []


def test_sec_filings_history_rejects_a_malformed_accession_and_needs_an_identity(
    configured: Path, monkeypatch: pytest.MonkeyPatch, sec_history_transport: Recorder
) -> None:
    monkeypatch.setenv("QF_SEC_USER_AGENT", _SEC_UA)
    get_settings.cache_clear()
    assert (
        cli.main(["ingestion", "sec-filings-history", "320193", "--accession", "not-an-accession"])
        == cli.EXIT_USAGE
    )
    assert sec_history_transport.paths == []
    monkeypatch.delenv("QF_SEC_USER_AGENT", raising=False)
    get_settings.cache_clear()
    assert cli.main(["ingestion", "sec-filings-history", "320193"]) == cli.EXIT_USAGE


# ── SEC FSDS financial facts (local file only: no network, no SEC contact) ──


def _seed_filings_for_fsds(engine: Engine, *, with_10qa: bool = True) -> None:

    forms = {
        ACC_10Q: ("10-Q", date(2024, 5, 3), datetime(2024, 5, 3, 20, 30, 22, tzinfo=UTC)),
        ACC_10K: ("10-K", date(2023, 11, 3), datetime(2023, 11, 3, 22, 8, 27, tzinfo=UTC)),
        ACC_10QA: ("10-Q/A", date(2024, 5, 17), datetime(2024, 5, 17, 13, 12, 40, tzinfo=UTC)),
    }
    with Session(engine) as session:
        company = Company(legal_name="Example Filer", display_name="Example", cik=CIK_MAIN)
        session.add(company)
        source = get_or_create_data_source(
            session, name="sec-edgar", provider_type=ProviderType.REGULATORY_FILING
        )
        session.flush()
        for accession, (form, filing_date, accepted) in forms.items():
            if accession == ACC_10QA and not with_10qa:
                continue
            session.add(
                SecFiling(
                    company_id=company.id,
                    accession_number=accession,
                    form=SecFormType(form),
                    filing_date=filing_date,
                    accepted_at=accepted,
                    source_id=source.id,
                    ingested_at=datetime(2026, 10, 3, tzinfo=UTC),
                    created_at=datetime(2026, 10, 3, tzinfo=UTC),
                )
            )
        session.commit()


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Fail loudly if the FSDS command ever builds an HTTP client of either provider."""
    calls: list[str] = []

    def forbid(name: str) -> Any:
        def explode(*_a: Any, **_k: Any) -> Any:
            calls.append(name)
            raise AssertionError(f"{name} must not be built by a local-file command")

        return explode

    monkeypatch.setattr(cli, "_sec_client", forbid("sec"))
    monkeypatch.setattr(cli, "_client", forbid("massive"))
    return calls


def test_sec_fsds_ingests_a_local_package_without_network_or_sec_contact(
    configured: Path,
    empty_database: None,
    monkeypatch: pytest.MonkeyPatch,
    no_network: list[str],
    integration_engine: Engine,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:

    monkeypatch.delenv("QF_SEC_USER_AGENT", raising=False)
    get_settings.cache_clear()
    _seed_filings_for_fsds(integration_engine)
    package = build_package(tmp_path / "2024q2.zip")

    code = cli.main(
        ["ingestion", "sec-fsds", str(package), "--accession", ACC_10Q, "--accession", ACC_10K]
    )

    assert code == cli.EXIT_OK
    assert no_network == []
    out = capsys.readouterr().out
    assert "2024q2: received=13 written=13 skipped=0 failed=0" in out
    with Session(integration_engine) as session:
        assert session.query(FinancialFact).count() == 13
        artifact = session.query(FinancialFactArtifact).one()
        assert artifact.artifact_name == "2024q2"  # derived from the file name
        run = session.query(IngestionRun).filter_by(dataset="sec-fsds").one()
        assert run.status is IngestionStatus.SUCCEEDED
        assert str(tmp_path) not in json.dumps(run.request_parameters)
    assert code == cli.main(
        ["ingestion", "sec-fsds", str(package), "--accession", ACC_10Q, "--accession", ACC_10K]
    )
    assert "skipped=13" in capsys.readouterr().out


def test_sec_fsds_reports_unbound_accessions_for_a_cik_selection(
    configured: Path,
    empty_database: None,
    no_network: list[str],
    integration_engine: Engine,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:

    _seed_filings_for_fsds(integration_engine, with_10qa=False)
    package = build_package(tmp_path / "2024q2.zip")

    code = cli.main(["ingestion", "sec-fsds", str(package), "--cik", "320193"])

    assert code == cli.EXIT_OK
    out = capsys.readouterr().out
    assert f"unbound (no stored SecFiling; nothing written): {ACC_10QA}" in out
    assert "partial" in out


@pytest.mark.parametrize(
    "argv_tail",
    [[], ["--accession", "bad"], ["--cik", "x"]],
)
def test_sec_fsds_refuses_an_unbounded_or_malformed_selection(
    configured: Path,
    empty_database: None,
    no_network: list[str],
    integration_engine: Engine,
    tmp_path: Path,
    argv_tail: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:

    package = build_package(tmp_path / "2024q2.zip")
    assert cli.main(["ingestion", "sec-fsds", str(package), *argv_tail]) == cli.EXIT_USAGE
    assert "error:" in capsys.readouterr().err
    with Session(integration_engine) as session:
        assert session.query(IngestionRun).count() == 0


def test_sec_fsds_needs_a_package_name_when_the_file_name_does_not_give_one(
    configured: Path,
    empty_database: None,
    no_network: list[str],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:

    package = build_package(tmp_path / "download (1).zip")
    code = cli.main(["ingestion", "sec-fsds", str(package), "--accession", ACC_10Q])
    assert code == cli.EXIT_USAGE
    assert "--package-name" in capsys.readouterr().err


def test_sec_fsds_missing_filing_is_a_usage_error_with_a_durable_failed_run(
    configured: Path,
    empty_database: None,
    no_network: list[str],
    integration_engine: Engine,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:

    package = build_package(tmp_path / "2024q2.zip")
    code = cli.main(["ingestion", "sec-fsds", str(package), "--accession", ACC_10Q])
    assert code == cli.EXIT_USAGE
    assert "no stored SecFiling" in capsys.readouterr().err
    with Session(integration_engine) as session:
        assert session.query(FinancialFact).count() == 0
        assert session.query(SecFiling).count() == 0
        assert session.query(IngestionRun).filter_by(status=IngestionStatus.FAILED).count() == 1


def test_sec_fsds_a_revised_package_is_a_failed_run_and_never_applied(
    configured: Path,
    empty_database: None,
    no_network: list[str],
    integration_engine: Engine,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:

    _seed_filings_for_fsds(integration_engine)
    original = build_package(tmp_path / "2024q2.zip")
    assert cli.main(["ingestion", "sec-fsds", str(original), "--accession", ACC_10Q]) == cli.EXIT_OK
    capsys.readouterr()

    revised = build_package(
        tmp_path / "revised.zip",
        nums=[
            *default_nums(),
            num_row(
                adsh=ACC_10Q,
                tag="Extra",
                version="us-gaap/2024",
                ddate="20240331",
                qtrs="0",
                value="1.0000",
            ),
        ],
    )
    code = cli.main(
        [
            "ingestion",
            "sec-fsds",
            str(revised),
            "--package-name",
            "2024q2",
            "--accession",
            ACC_10Q,
        ]
    )
    assert code == cli.EXIT_RUN_FAILED
    err = capsys.readouterr().err
    assert "already ingested with different content" in err
    assert "nothing was overwritten" in err
    with Session(integration_engine) as session:
        assert session.query(FinancialFact).count() == 12
        assert session.query(IngestionRun).filter_by(status=IngestionStatus.FAILED).count() == 1
