"""``quantforge ingestion ...`` -- the operator-facing entry point to ingestion.

Why a CLI and not an HTTP endpoint
----------------------------------
Ingestion is an *operational* act, not a user-facing one. It needs a database
transaction, a filesystem it can write raw responses to, and a terminal to report a
partial failure on. An HTTP handler would have to fake all three: it would have to
either commit outside the request transaction or pretend not to, it would need
write access to the raw store from a web worker, and it would report "500" for
what is really "3 of 500 symbols failed, here they are". None of those failures is
an exceptional condition an operator should page on.

Making it a command also makes it *safe by default*: it is invoked deliberately, by
someone who knows which symbols and which date range they mean. A background job
that re-runs on a schedule is Step 7's problem, and by then the run table and the
idempotent writes here are what make it safe.

Exit codes
----------
``0`` succeeded, ``1`` the run failed, ``2`` the invocation was wrong (bad symbol,
missing key, unresolvable ticker). Distinguishing "your command was wrong" from
"the ingestion failed" matters for scripting: a missing key is a configuration
problem that should not be retried, whereas a failed run may be.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from quantforge.core.config import Settings, get_settings
from quantforge.db.session import get_session_factory
from quantforge.ingestion.benchmark import BenchmarkBarConflictError, BenchmarkIdentityConflict
from quantforge.ingestion.daily import ProviderDataConflict
from quantforge.ingestion.raw_store import RawResponseStore
from quantforge.ingestion.runs import RunOutcome
from quantforge.ingestion.service import IngestionService, UnknownListingError
from quantforge.models.ingestion import IngestionRun
from quantforge.providers.errors import (
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderError,
)
from quantforge.providers.massive.client import MassiveClient, MassiveConfig

__all__ = ["main"]

logger = logging.getLogger("quantforge.ingestion.cli")

EXIT_OK = 0
EXIT_RUN_FAILED = 1
EXIT_USAGE = 2


def _iso_date(value: str) -> date:
    """Parse ``YYYY-MM-DD``, rejecting anything else with a usable message."""
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        message = f"{value!r} is not a date; use YYYY-MM-DD"
        raise argparse.ArgumentTypeError(message) from exc


def _ticker(value: str) -> str:
    """Normalise a symbol to the upper-case form the schema stores."""
    cleaned = value.strip().upper()
    if not cleaned or not cleaned.replace(".", "").replace("-", "").isalnum():
        message = f"{value!r} does not look like a ticker symbol"
        raise argparse.ArgumentTypeError(message)
    return cleaned


def build_parser() -> argparse.ArgumentParser:
    """The argument parser.

    Exposed for testing, so the wiring can be asserted without spawning a
    process. Nothing here reads settings or touches a database, so ``--help``
    works with no configuration and no network -- which matters, because the most
    likely first thing a new user does is ask the tool what it can do.
    """
    parser = argparse.ArgumentParser(
        prog="quantforge",
        description="Ingest US equity reference data, daily bars and benchmark levels from Massive.",
    )
    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="stderr log verbosity (default: WARNING)",
    )
    sub = parser.add_subparsers(dest="group", required=True)

    # Grouped under "ingestion" because the same console script will grow
    # commands for things that are not ingestion. Flattening them now and
    # re-nesting later breaks every script and alias that uses the old form.
    ingestion = sub.add_parser(
        "ingestion",
        help="fetch and manage market data",
        description="Ingest US equity reference data, daily bars and benchmark levels from Massive.",
    )
    commands = ingestion.add_subparsers(dest="command", required=True)

    reference = commands.add_parser(
        "reference",
        help="resolve ticker reference data and store the resulting listings",
        description=(
            "Fetch reference data for one or more symbols and store the "
            "companies, securities and listings they identify. Safe to re-run: "
            "an already-resolved symbol is matched, not duplicated."
        ),
    )
    reference.add_argument("tickers", nargs="+", type=_ticker, help="e.g. AAPL MSFT")
    reference.set_defaults(handler=_cmd_reference)

    daily = commands.add_parser(
        "daily",
        help="fetch daily bars for an already-ingested symbol",
        description=(
            "Fetch daily OHLCV bars over an inclusive date range. The symbol "
            "must already be ingested by 'quantforge ingestion reference', "
            "because a bar's session date is derived through its venue's "
            "timezone."
        ),
    )
    daily.add_argument("ticker", type=_ticker, help="e.g. AAPL")
    daily.add_argument("--start", required=True, type=_iso_date, help="inclusive YYYY-MM-DD")
    daily.add_argument("--end", required=True, type=_iso_date, help="inclusive YYYY-MM-DD")
    daily.add_argument(
        "--exchange",
        dest="exchange_mic",
        help="ISO 10383 MIC, required only if the symbol is listed on several venues",
    )
    daily.set_defaults(handler=_cmd_daily)

    benchmark = commands.add_parser(
        "benchmark",
        help="fetch daily levels for a benchmark instrument (e.g. an ETF proxy)",
        description=(
            "Fetch daily aggregate levels for a benchmark symbol over an inclusive "
            "date range, from the same Massive daily-aggregate endpoint "
            "'quantforge ingestion daily' uses, and store them against an "
            "explicit Benchmark identity. There is no implied default benchmark: "
            "--name must state exactly what the symbol represents. Example: "
            "quantforge ingestion benchmark SPY --start 2026-09-01 --end "
            "2026-09-30 --name 'SPY (ETF proxy for the S&P 500)'."
        ),
    )
    benchmark.add_argument("symbol", type=_ticker, help="e.g. SPY")
    benchmark.add_argument("--start", required=True, type=_iso_date, help="inclusive YYYY-MM-DD")
    benchmark.add_argument("--end", required=True, type=_iso_date, help="inclusive YYYY-MM-DD")
    benchmark.add_argument(
        "--name",
        required=True,
        help=(
            "explicit benchmark identity, e.g. 'SPY (ETF proxy for the S&P 500)' "
            "-- never inferred from the symbol, and never 'S&P 500 Index' for an "
            "ETF proxy"
        ),
    )
    benchmark.add_argument(
        "--currency",
        default="USD",
        help="ISO 4217 code, recorded only if this creates the Benchmark row (default: USD)",
    )
    benchmark.set_defaults(handler=_cmd_benchmark)

    runs = commands.add_parser(
        "runs",
        help="list recent ingestion runs, newest first",
        description="Read-only. Shows what ran, whether it worked, and where its raw data is.",
    )
    runs.add_argument("--limit", type=int, default=10, help="rows to show (default: 10)")
    runs.add_argument("--dataset", help="only runs of this dataset, e.g. reference or daily-bars")
    runs.set_defaults(handler=_cmd_runs)

    return parser


@dataclass(frozen=True, slots=True)
class DailyRequest:
    """A validated daily-bar request, as the user expressed it.

    Grouped rather than passed as five parameters so the "which symbol, over what
    range, on which venue" question is answered in one place, and so the service
    call site reads as the sentence it is.
    """

    ticker: str
    start: date
    end: date
    exchange_mic: str | None = None


@dataclass(frozen=True, slots=True)
class BenchmarkRequest:
    """A validated benchmark-ingestion request, as the user expressed it."""

    symbol: str
    start: date
    end: date
    name: str
    currency: str | None = None


# ── Command handlers ────────────────────────────────────────────────────────


def _cmd_reference(args: argparse.Namespace, settings: Settings) -> int:
    async def run() -> tuple[RunOutcome, list[Any]]:
        async with _client(settings) as client:
            return await _ingest(
                settings=settings,
                client=client,
                tickers=args.tickers,
                now=datetime.now(UTC),
            )

    outcome, results = asyncio.run(run())
    _print_reference_results(args.tickers, results)
    return _print_outcome(outcome)


def _cmd_daily(args: argparse.Namespace, settings: Settings) -> int:
    if args.end < args.start:
        message = f"--end {args.end} is before --start {args.start}"
        print(f"error: {message}", file=sys.stderr)
        return EXIT_USAGE

    request = DailyRequest(
        ticker=args.ticker,
        start=args.start,
        end=args.end,
        exchange_mic=args.exchange_mic,
    )

    async def run() -> RunOutcome:
        async with _client(settings) as client:
            return await _ingest_daily(
                settings=settings, client=client, request=request, now=datetime.now(UTC)
            )

    outcome = asyncio.run(run())
    print(
        f"\n{request.ticker} {request.start}..{request.end}: "
        f"received={outcome.records_received} written={outcome.records_written} "
        f"skipped={outcome.records_skipped} failed={outcome.records_failed}"
    )
    return _print_outcome(outcome)


def _cmd_benchmark(args: argparse.Namespace, settings: Settings) -> int:
    if args.end < args.start:
        message = f"--end {args.end} is before --start {args.start}"
        print(f"error: {message}", file=sys.stderr)
        return EXIT_USAGE

    request = BenchmarkRequest(
        symbol=args.symbol,
        start=args.start,
        end=args.end,
        name=args.name,
        currency=args.currency,
    )

    async def run() -> RunOutcome:
        async with _client(settings) as client:
            return await _ingest_benchmark(
                settings=settings, client=client, request=request, now=datetime.now(UTC)
            )

    outcome = asyncio.run(run())
    print(
        f"\n{request.symbol} ({request.name}) {request.start}..{request.end}: "
        f"received={outcome.records_received} written={outcome.records_written} "
        f"skipped={outcome.records_skipped} failed={outcome.records_failed}"
    )
    return _print_outcome(outcome)


def _cmd_runs(args: argparse.Namespace, settings: Settings) -> int:
    if args.limit < 1:
        print("error: --limit must be at least 1", file=sys.stderr)
        return EXIT_USAGE
    factory = get_session_factory(settings)
    with factory() as session:
        query = sa.select(IngestionRun).order_by(IngestionRun.started_at.desc())
        if args.dataset:
            query = query.where(IngestionRun.dataset == args.dataset)
        rows = list(session.scalars(query.limit(args.limit)))
        if not rows:
            print("no runs recorded")
            return EXIT_OK
        for row in rows:
            _print_run_row(row)
    return EXIT_OK


# ── Wiring ──────────────────────────────────────────────────────────────────


def _client(settings: Settings) -> MassiveClient:
    """Build the adapter from settings, failing clearly if the key is absent."""
    config = MassiveConfig(
        api_key=settings.massive_api_key,
        base_url=settings.massive_base_url,
        request_timeout=settings.massive_request_timeout_seconds,
        max_retries=settings.massive_max_retries,
        max_pages=settings.massive_max_pages,
        retry_base_delay=settings.massive_retry_base_delay_seconds,
        min_request_interval=settings.massive_min_request_interval_seconds,
    )
    # Raises with an actionable message naming the variable to set, rather than
    # letting an auth failure look like a provider outage half a minute later.
    config.require_api_key()
    return MassiveClient(config)


async def _ingest(
    *,
    settings: Settings,
    client: MassiveClient,
    tickers: Sequence[str],
    now: datetime,
) -> tuple[RunOutcome, list[Any]]:
    """Run reference ingestion, committing on success and rolling back on failure.

    The commit lives here rather than inside the service so the service stays
    usable from a caller that is already inside a larger transaction -- a future
    scheduler coordinating several symbols, say. The service adds rows and
    finalises its run; when to make that durable is the caller's decision.
    """
    factory = get_session_factory(settings)
    with factory() as session:
        service = IngestionService(
            session=session,
            provider=client,
            store=_store(settings),
            now=now,
        )
        try:
            outcome, results = await service.ingest_reference(tickers)
        except BaseException:
            session.rollback()
            raise
        session.commit()
        return outcome, results


async def _ingest_daily(
    *,
    settings: Settings,
    client: MassiveClient,
    request: DailyRequest,
    now: datetime,
) -> RunOutcome:
    factory = get_session_factory(settings)
    with factory() as session:
        service = IngestionService(
            session=session,
            provider=client,
            store=_store(settings),
            now=now,
        )
        try:
            outcome = await service.ingest_daily_bars(
                request.ticker,
                request.start,
                request.end,
                exchange_mic=request.exchange_mic,
            )
        except BaseException:
            session.rollback()
            raise
        session.commit()
        return outcome


async def _ingest_benchmark(
    *,
    settings: Settings,
    client: MassiveClient,
    request: BenchmarkRequest,
    now: datetime,
) -> RunOutcome:
    factory = get_session_factory(settings)
    with factory() as session:
        service = IngestionService(
            session=session,
            provider=client,
            store=_store(settings),
            now=now,
        )
        try:
            outcome = await service.ingest_benchmark_daily_bars(
                request.symbol,
                request.start,
                request.end,
                name=request.name,
                currency=request.currency,
            )
        except BaseException:
            session.rollback()
            raise
        session.commit()
        return outcome


def _store(settings: Settings) -> RawResponseStore:
    return RawResponseStore(root=Path(settings.ingestion_raw_root))


# ── Output ──────────────────────────────────────────────────────────────────


def _print_reference_results(tickers: Sequence[str], results: Sequence[Any]) -> None:
    """Report per-symbol resolution, including symbols the provider does not have.

    A symbol silently absent from the output would be indistinguishable from one
    that was never requested, so every requested symbol gets a line: resolved,
    unknown to the provider, or failed.
    """
    by_ticker = {r.ticker: r for r in results}
    created = sum(1 for r in results if r.created)
    print(f"\nrequested {len(tickers)} symbol(s); {len(results)} resolved, {created} newly created")
    for ticker in tickers:
        found = by_ticker.get(ticker)
        if found is None:
            # The service logged why. Kept terse here so 500 lines of output stay
            # readable, but the symbol is still visibly accounted for.
            print(f"  {ticker:<8} not resolved (see log output above)")
            continue
        action = "created" if found.created else "matched"
        print(
            f"  {ticker:<8} {action} on {found.matched_on} "
            f"company={found.company_id[:8]} security={found.security_id[:8]} "
            f"listing={found.listing_id[:8]}"
        )


def _print_outcome(outcome: RunOutcome) -> int:
    """Print the run summary and map the status onto an exit code."""
    run = outcome.run
    print(f"run {run.id} [{run.provider}/{run.dataset}] {run.status.value}")
    if run.raw_artifact_path:
        print(f"  raw: {run.raw_artifact_path}")
    if run.error_message:
        print(f"  error: {run.error_message}")
    if run.status.value == "failed":
        return EXIT_RUN_FAILED
    if run.status.value == "partial":
        # Partial is a success with a shortfall, so it stays 0 -- but it is
        # reported, and a caller who cares can read the failed count.
        return EXIT_OK
    return EXIT_OK


def _print_run_row(row: IngestionRun) -> None:
    counts = (
        f"recv={row.records_received} wrote={row.records_written} "
        f"skip={row.records_skipped} fail={row.records_failed}"
    )
    print(
        f"{row.started_at:%Y-%m-%d %H:%M} {row.provider}/{row.dataset:<13} "
        f"{row.status.value:<9} {counts}"
    )
    if row.error_message:
        print(f"    error: {row.error_message}")


# ── Entry point ─────────────────────────────────────────────────────────────


def main(argv: Sequence[str] | None = None) -> int:
    """Console-script entry point. Returns the process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    try:
        settings = get_settings()
    except Exception as exc:
        print(f"error: cannot load configuration: {exc}", file=sys.stderr)
        return EXIT_USAGE

    handler: Any = args.handler
    try:
        return int(handler(args, settings))
    except Exception as exc:
        return _classify(exc)


def _classify(exc: Exception) -> int:  # noqa: PLR0911 - one return per distinct, named failure kind; splitting it up would not simplify anything
    """Map a failure onto an exit code and say why on stderr.

    Split out of :func:`main` so the ordering of the ``except`` clauses stays
    readable. Order matters: a bar conflict is a data decision, an auth failure is
    a configuration problem, and both are subclasses of the broader provider and
    lookup errors, so the specific cases have to be tried first.
    """
    if isinstance(exc, UnknownListingError):
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    if isinstance(exc, BenchmarkIdentityConflict):
        # Like UnknownListingError, this is the caller needing to say which
        # existing identity is meant (or use a different symbol/name), not a
        # data revision to decide about.
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    if isinstance(exc, ProviderAuthenticationError):
        print(f"error: authentication failed: {exc}", file=sys.stderr)
        return EXIT_USAGE
    if isinstance(exc, ProviderConfigurationError):
        # A missing or malformed credential is something the caller fixes in
        # their environment. Reporting it as a failed run would suggest retrying,
        # which cannot help, and would bury it among genuine provider outages.
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    if isinstance(exc, ProviderDataConflict | BenchmarkBarConflictError):
        print(f"error: {exc}", file=sys.stderr)
        print(
            "nothing was overwritten; reconcile the source before re-running",
            file=sys.stderr,
        )
        return EXIT_RUN_FAILED
    if isinstance(exc, ProviderError):
        print(f"error: provider request failed: {exc}", file=sys.stderr)
        return EXIT_RUN_FAILED
    if isinstance(exc, KeyboardInterrupt):  # pragma: no cover - interactive
        print("\ninterrupted", file=sys.stderr)
        return EXIT_RUN_FAILED
    # An unrecognised failure is a bug, not a usage problem. Say so, and re-raise
    # a traceback rather than swallowing a traceback that would have found it.
    print(f"error: unexpected failure: {type(exc).__name__}: {exc}", file=sys.stderr)
    raise exc from None


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
