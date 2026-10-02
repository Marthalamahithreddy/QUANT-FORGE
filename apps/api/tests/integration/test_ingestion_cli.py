"""End-to-end tests for the ``quantforge ingestion`` command.

These run the real entry point -- argument parsing, the service, the database, the
raw store and the run table -- against a mocked HTTP transport. The mock replaces
only the network, because that is the one part of the path that is neither ours
nor deterministic. A test that stubbed the service too would pass while the wiring
between them was broken, which is exactly the wiring an operator depends on.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from quantforge.core.config import get_settings
from quantforge.ingestion import cli
from quantforge.models.company import Company
from quantforge.models.enums import IngestionStatus
from quantforge.models.ingestion import IngestionRun
from quantforge.models.market import DailyPriceBar
from quantforge.models.security import Listing, Security
from quantforge.providers.errors import ProviderConfigurationError
from quantforge.providers.massive.client import MassiveClient, MassiveConfig

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
