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
import re
import sys
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from quantforge.core.config import Settings, get_settings
from quantforge.db.session import get_session_factory
from quantforge.identity.resolver import HistoricalListingResolver
from quantforge.ingestion.benchmark import BenchmarkBarConflictError, BenchmarkIdentityConflict
from quantforge.ingestion.daily import ProviderDataConflict
from quantforge.ingestion.filing_history import (
    DEFAULT_MAX_HISTORY_FILES,
    FilingHistoryIngestionService,
    FilingHistoryReport,
    InvalidHistoryRequestError,
)
from quantforge.ingestion.filings import (
    AllFilingRecordsRejectedError,
    FilingIngestionService,
    SecFilingConflictError,
    UnknownRegistrantError,
)
from quantforge.ingestion.financial_facts import (
    AccessionCikMismatchError,
    AccessionNotInPackageError,
    FinancialFactConflictError,
    FinancialFactIngestionService,
    FsdsRowsRejectedError,
    InvalidFactRequestError,
    MissingSecFilingError,
    NothingToIngestError,
    PackageRevisionError,
    SubmissionMetadataMismatchError,
    UnsupportedFormError,
)
from quantforge.ingestion.listing_history import (
    IncompleteListingHistoryError,
    InvalidAnchorDateError,
    ListingFactConflictError,
    ListingHistoryIngestionService,
)
from quantforge.ingestion.raw_store import RawResponseStore, RawStoreError
from quantforge.ingestion.runs import FAILED_RUN_RECORDED, RunOutcome
from quantforge.ingestion.service import IngestionService, UnknownListingError
from quantforge.ingestion.splits import (
    COVERAGE_SAFETY_MARGIN_DAYS,
    AmbiguousSplitSecurityError,
    IncompleteSplitFetchError,
    InvalidSplitRangeError,
    ListingHistoryConflictError,
    RetiredTickerCoverageError,
    SplitIngestionService,
    StockSplitConflictError,
    UnknownSplitTickerError,
    UnmappedSplitError,
)
from quantforge.models.enums import PriceBasis
from quantforge.models.ingestion import IngestionRun
from quantforge.providers.errors import (
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderError,
)
from quantforge.providers.massive.client import MassiveClient, MassiveConfig
from quantforge.providers.sec.client import SecClient, SecConfig, normalise_cik
from quantforge.providers.sec.fsds import FsdsPackageError
from quantforge.providers.sec.history import HistoryScopeError

__all__ = ["main"]

logger = logging.getLogger("quantforge.ingestion.cli")

EXIT_OK = 0
EXIT_RUN_FAILED = 1
EXIT_USAGE = 2

#: Massive's Basic plan serves only about two years of split history. Coverage claimed
#: for older dates on such a plan would be false, and the plan cannot be detected from
#: a response, so an older start needs the operator to attest to a full-history plan.
_BASIC_PLAN_SPLIT_HISTORY_DAYS = 700


def _iso_date(value: str) -> date:
    """Parse ``YYYY-MM-DD``, rejecting anything else with a usable message."""
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        message = f"{value!r} is not a date; use YYYY-MM-DD"
        raise argparse.ArgumentTypeError(message) from exc


_PRICE_BASIS_CHOICES: dict[str, PriceBasis] = {
    "split-adjusted": PriceBasis.SPLIT_ADJUSTED,
    "as-traded": PriceBasis.AS_TRADED,
}


def _add_price_basis_argument(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--price-basis",
        dest="price_basis",
        choices=sorted(_PRICE_BASIS_CHOICES),
        default="split-adjusted",
        help=(
            "split-adjusted (default; Massive adjusted=true -- splits only, dividends "
            "excluded -- the research basis) or as-traded (adjusted=false; the "
            "execution/accounting candidate). Each basis is stored as its own rows."
        ),
    )


def _ticker(value: str) -> str:
    """Normalise a symbol to the upper-case form the schema stores."""
    cleaned = value.strip().upper()
    if not cleaned or not cleaned.replace(".", "").replace("-", "").isalnum():
        message = f"{value!r} does not look like a ticker symbol"
        raise argparse.ArgumentTypeError(message)
    return cleaned


def _add_listing_history_commands(commands: Any) -> None:
    """The sourced listing-history commands: snapshot, events, and a read-only era report."""
    snapshot = commands.add_parser(
        "listing-snapshot",
        help="store a dated ticker snapshot as a sourced listing-history fact",
        description=(
            "Fetch Massive's listing of one ticker as of ONE date (/v3/reference/tickers?date=) and "
            "store the facts it states for that single date (the ticker was observed that day; a "
            "last-traded date when the provider gives one). Each record is attributed to a stored "
            "security through its share-class FIGI. Nothing is derived and the listing table is "
            "never changed; two snapshots do not make a continuous interval."
        ),
    )
    snapshot.add_argument("ticker", type=_ticker, help="e.g. META")
    snapshot.add_argument(
        "--date", required=True, type=_iso_date, help="the snapshot date, YYYY-MM-DD"
    )
    snapshot.add_argument(
        "--delisted",
        action="store_true",
        help="ask for tickers NOT trading on that date (to read a last-traded date)",
    )
    snapshot.set_defaults(handler=_cmd_listing_snapshot)

    events = commands.add_parser(
        "listing-events",
        help="store a ticker's ticker-change history as sourced listing-history facts",
        description=(
            "Fetch Massive's EXPERIMENTAL ticker-events for the entity currently represented by "
            "TICKER and store each ticker change as a 'ticker started' fact. Identity is anchored "
            "by a dated snapshot fetched in the same run (--anchor-date, default yesterday UTC): "
            "its share-class FIGI decides which stored security the events belong to. Everything "
            "is stored or nothing is. No exchange continuity is implied."
        ),
    )
    events.add_argument("ticker", type=_ticker, help="the ticker the entity holds NOW, e.g. META")
    events.add_argument(
        "--anchor-date",
        type=_iso_date,
        default=None,
        help="a recent date the ticker traded (default: yesterday, UTC)",
    )
    events.set_defaults(handler=_cmd_listing_events)

    eras = commands.add_parser(
        "listing-eras",
        help="show the ticker eras the stored evidence proves (read-only, no provider call)",
        description=(
            "Print where, inside the date range, one security is PROVEN to have held the ticker, "
            "using the same resolver research, the earnings API and split ingestion use. A gap, an "
            "ambiguity or a conflict is reported as such, never filled."
        ),
    )
    eras.add_argument("ticker", type=_ticker, help="e.g. META")
    eras.add_argument("--start", required=True, type=_iso_date, help="inclusive YYYY-MM-DD")
    eras.add_argument("--end", required=True, type=_iso_date, help="inclusive YYYY-MM-DD")
    eras.set_defaults(handler=_cmd_listing_eras)


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
    _add_price_basis_argument(daily)
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
    _add_price_basis_argument(benchmark)
    benchmark.set_defaults(handler=_cmd_benchmark)

    sec = commands.add_parser(
        "sec-filings",
        help="fetch SEC filing metadata (10-K/10-Q/8-K and amendments) for a stored company",
        description=(
            "Fetch filing metadata -- accession, form, filing/report dates, EDGAR acceptance "
            "time, primary document -- from the SEC submissions API for the stored company "
            "that carries this CIK. Metadata only: no filing documents, no EPS. Requires "
            "QF_SEC_USER_AGENT (an application name plus a contact), per the SEC's "
            "fair-access policy. The acceptance time proves EDGAR acceptance, not first "
            "public disclosure."
        ),
    )
    sec.add_argument("cik", help="the registrant's CIK, 1-10 digits (zero-padded automatically)")
    sec.set_defaults(handler=_cmd_sec_filings)

    history = commands.add_parser(
        "sec-filings-history",
        help="fetch OLDER SEC filing identity (history files) for ONE stored company",
        description=(
            "Store filing IDENTITY metadata (accession, form, filing/report dates, EDGAR "
            "acceptance time, primary document) for the stored company that carries this "
            "CIK, from its submissions response AND the history files that response names "
            "(older than filings.recent), so older accession-bound FSDS facts can bind to a "
            "stored filing. One explicit CIK per run: one parent request plus at most "
            "--max-history-files history-file requests, never another registrant and no "
            "'all companies' mode. With --accession, only history files whose stated range "
            "could hold a still-unresolved accession are fetched. Identity and provenance "
            "only: no availability, PIT or disclosure meaning, no facts, no EPS. "
            "Requires QF_SEC_USER_AGENT."
        ),
    )
    history.add_argument(
        "cik", help="the registrant's CIK, 1-10 digits (zero-padded automatically)"
    )
    history.add_argument(
        "--accession",
        action="append",
        default=[],
        help=(
            "a dashed target accession (0000320193-14-000123); repeatable. Limits the history "
            "files fetched to those that could hold an unresolved target; a target that is "
            "not found is reported as unresolved, never as absent from the SEC"
        ),
    )
    history.add_argument(
        "--max-history-files",
        type=int,
        default=DEFAULT_MAX_HISTORY_FILES,
        help=(
            f"explicit upper bound on history-file requests (default {DEFAULT_MAX_HISTORY_FILES}); "
            "a registrant needing more is refused before any history request, not truncated"
        ),
    )
    history.set_defaults(handler=_cmd_sec_filings_history)

    splits = commands.add_parser(
        "stock-splits",
        help="fetch a ticker's historical stock splits and record split coverage",
        description=(
            "Fetch every split Massive holds for one ticker (Massive /stocks/v1/splits), store "
            "those in the inclusive date range against the security that held the ticker on "
            "each split's execution date, and -- only if the whole fetch is complete and "
            "clean -- record split COVERAGE for the range. Coverage is what lets a later "
            "'no split found' mean 'no split existed'. --end must be at least "
            f"{COVERAGE_SAFETY_MARGIN_DAYS} days before today. Coverage is recorded only through a "
            "current ticker of the security. The ticker "
            "must already be ingested by 'quantforge ingestion reference'. Nothing is "
            "adjusted. One ticker per run; this is not a bulk download."
        ),
    )
    splits.add_argument("ticker", type=_ticker, help="e.g. NVDA")
    splits.add_argument("--start", required=True, type=_iso_date, help="inclusive YYYY-MM-DD")
    splits.add_argument(
        "--end",
        required=True,
        type=_iso_date,
        help=f"inclusive YYYY-MM-DD, at least {COVERAGE_SAFETY_MARGIN_DAYS} days before today",
    )
    splits.add_argument(
        "--full-history-plan",
        action="store_true",
        help=(
            "state that your Massive plan serves all split history. Required when --start is "
            "more than about two years ago (over 700 days), because the Basic plan returns only two years and "
            "coverage claimed beyond that would be false"
        ),
    )
    splits.set_defaults(handler=_cmd_stock_splits)

    fsds = commands.add_parser(
        "sec-fsds",
        help="extract accession-bound financial facts from a local SEC FSDS quarterly ZIP",
        description=(
            "Read facts for the NAMED accessions and/or CIKs from one local SEC Financial "
            "Statement Data Sets quarterly ZIP (sub.txt + num.txt), bind them to filings "
            "already stored by 'quantforge ingestion sec-filings', and store them with the "
            "package's SHA-256 as provenance. Bounded: at least one --accession or --cik is "
            "required; there is no whole-package or whole-archive mode. Local file only: no "
            "network request is made and no SEC contact is needed. Facts are source concepts, "
            "not EPS/revenue: nothing is selected, derived or compared, and a fact is NOT "
            "point-in-time available at its filing's acceptance time (a bulk package is "
            "published later)."
        ),
    )
    fsds.add_argument("package", type=Path, help="path to a downloaded FSDS ZIP, e.g. 2025q3.zip")
    fsds.add_argument(
        "--package-name",
        help=(
            "the package's logical identity, e.g. 2025q3; defaults to the file name without "
            "'.zip' when that has the form YYYYqN"
        ),
    )
    fsds.add_argument(
        "--accession",
        action="append",
        default=[],
        help="a dashed accession (0000320193-24-000123); repeatable",
    )
    fsds.add_argument(
        "--cik",
        action="append",
        default=[],
        help="a registrant CIK; every 10-K/10-Q submission of it in the package; repeatable",
    )
    fsds.set_defaults(handler=_cmd_sec_fsds)

    _add_listing_history_commands(commands)

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
    #: CLI default only: the documented research basis. The service layer has
    #: no default and requires the basis explicitly.
    price_basis: PriceBasis = PriceBasis.SPLIT_ADJUSTED


@dataclass(frozen=True, slots=True)
class BenchmarkRequest:
    """A validated benchmark-ingestion request, as the user expressed it."""

    symbol: str
    start: date
    end: date
    name: str
    currency: str | None = None
    price_basis: PriceBasis = PriceBasis.SPLIT_ADJUSTED


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
        price_basis=_PRICE_BASIS_CHOICES[args.price_basis],
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
        price_basis=_PRICE_BASIS_CHOICES[args.price_basis],
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


def _cmd_sec_filings(args: argparse.Namespace, settings: Settings) -> int:
    try:
        cik = normalise_cik(args.cik)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    async def run() -> RunOutcome:
        async with _sec_client(settings) as client:
            return await _ingest_sec_filings(
                settings=settings, client=client, cik=cik, now=datetime.now(UTC)
            )

    outcome = asyncio.run(run())
    print(
        f"\nCIK {cik}: received={outcome.records_received} written={outcome.records_written} "
        f"skipped={outcome.records_skipped} failed={outcome.records_failed}"
    )
    return _print_outcome(outcome)


def _cmd_sec_filings_history(args: argparse.Namespace, settings: Settings) -> int:
    try:
        cik = normalise_cik(args.cik)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    async def run() -> FilingHistoryReport:
        async with _sec_client(settings) as client:
            factory = get_session_factory(settings)
            with factory() as session:
                service = FilingHistoryIngestionService(
                    session=session, provider=client, store=_store(settings), now=datetime.now(UTC)
                )
                try:
                    report = await service.ingest_filing_history(
                        cik,
                        accessions=args.accession,
                        max_history_files=args.max_history_files,
                    )
                except BaseException:
                    _commit_failed_run(session)
                    raise
                session.commit()
                return report

    report = asyncio.run(run())
    result = report.result
    outcome = report.outcome
    print(
        f"\nCIK {cik}: received={outcome.records_received} written={outcome.records_written} "
        f"skipped={outcome.records_skipped} failed={outcome.records_failed}"
    )
    print(
        f"  recent rows seen: {result.recent_rows_seen} "
        f"(supported-form filings parsed: {result.recent_filings_parsed})"
    )
    print(
        f"  history files: discovered {result.history_files_discovered}, "
        f"fetched {result.history_files_fetched}, "
        f"skipped by the accession scope {result.history_files_skipped}"
    )
    print(
        f"  history rows seen: {result.history_rows_seen} "
        f"(supported-form filings parsed: {result.history_filings_parsed})"
    )
    print(
        f"  stored: inserted {report.inserted}, matched existing {report.matched_existing}; "
        f"seen in both recent and history with identical metadata: {result.overlap_identical}"
    )
    if result.requested_accessions:
        print(
            f"  requested accessions: {len(result.requested_accessions)}, "
            f"unresolved in the fetched scope: {len(result.unresolved_accessions)}"
        )
        for accession in result.located_not_stored:
            print(
                f"    located but not stored: {accession} (present in a fetched block as an "
                "unsupported form or a rejected record; see the rejected reasons)"
            )
        for accession in result.unresolved_accessions:
            print(
                f"    unresolved: {accession} (not found in what was fetched; NOT proof of absence)"
            )
    print(
        "  scope: identity metadata only for the fetched files; this does not claim the "
        "registrant's history is complete and carries no availability meaning"
    )
    return _print_outcome(outcome)


def _cmd_sec_fsds(args: argparse.Namespace, settings: Settings) -> int:
    package: Path = args.package
    package_name: str | None = args.package_name or (
        package.stem if re.fullmatch(r"[0-9]{4}q[1-4]", package.stem) else None
    )
    if package_name is None:
        print(
            "error: cannot derive the package name from the file name; pass --package-name "
            "(e.g. 2025q3)",
            file=sys.stderr,
        )
        return EXIT_USAGE
    now = datetime.now(UTC)
    with get_session_factory(settings)() as session:
        service = FinancialFactIngestionService(session=session, store=_store(settings), now=now)
        try:
            result = service.ingest_fsds_package(
                package,
                package_name=package_name,
                accessions=args.accession,
                ciks=args.cik,
            )
        except BaseException:
            _commit_failed_run(session)
            raise
        session.commit()
    outcome = result.outcome
    print(
        f"\n{package_name}: received={outcome.records_received} written={outcome.records_written} "
        f"skipped={outcome.records_skipped} failed={outcome.records_failed}"
    )
    selected = (
        len(result.bound_accessions)
        + len(result.unbound_accessions)
        + len(result.out_of_scope_forms)
    )
    print(
        f"  selected submissions: {selected} (bound {len(result.bound_accessions)}, "
        f"unbound {len(result.unbound_accessions)}, not read {len(result.out_of_scope_forms)})"
    )
    for accession in result.unbound_accessions:
        print(f"  unbound (no stored SecFiling; nothing written): {accession}")
    for accession, form in result.out_of_scope_forms:
        print(f"  not read (form {form}): {accession}")
    if result.unbound_accessions:
        print(
            "  partial: a fact missing from the database is NOT evidence the source lacks it "
            "(unbound accessions were not processed)"
        )
    return _print_outcome(outcome)


def _cmd_stock_splits(args: argparse.Namespace, settings: Settings) -> int:
    if args.end < args.start:
        print(f"error: --end {args.end} is before --start {args.start}", file=sys.stderr)
        return EXIT_USAGE
    now = datetime.now(UTC)
    oldest_safe = now.date() - timedelta(days=_BASIC_PLAN_SPLIT_HISTORY_DAYS)
    # The plan cannot be detected from a response, so an older start needs the operator to
    # attest to a full-history plan. The service enforces the bound it is given.
    history_start = None if args.full_history_plan else oldest_safe

    async def run() -> RunOutcome:
        async with _client(settings) as client:
            return await _ingest_stock_splits(
                settings=settings,
                client=client,
                ticker=args.ticker,
                start=args.start,
                end=args.end,
                now=now,
                provider_history_start=history_start,
            )

    outcome = asyncio.run(run())
    print(
        f"\n{args.ticker} splits {args.start}..{args.end}: received={outcome.records_received} "
        f"written={outcome.records_written} skipped={outcome.records_skipped} "
        f"failed={outcome.records_failed}"
    )
    return _print_outcome(outcome)


def _cmd_listing_snapshot(args: argparse.Namespace, settings: Settings) -> int:
    now = datetime.now(UTC)

    async def run() -> RunOutcome:
        async with _client(settings) as client:
            return await _ingest_listing_history(
                settings=settings,
                client=client,
                now=now,
                operation=lambda service: service.ingest_ticker_snapshot(
                    args.ticker, args.date, active=not args.delisted
                ),
            )

    outcome = asyncio.run(run())
    print(
        f"\n{args.ticker} snapshot {args.date}: received={outcome.records_received} "
        f"written={outcome.records_written} skipped={outcome.records_skipped} "
        f"failed={outcome.records_failed}"
    )
    return _print_outcome(outcome)


def _cmd_listing_events(args: argparse.Namespace, settings: Settings) -> int:
    now = datetime.now(UTC)
    anchor = args.anchor_date or (now.date() - timedelta(days=1))

    async def run() -> RunOutcome:
        async with _client(settings) as client:
            return await _ingest_listing_history(
                settings=settings,
                client=client,
                now=now,
                operation=lambda service: service.ingest_ticker_events(
                    args.ticker, anchor_date=anchor
                ),
            )

    outcome = asyncio.run(run())
    print(
        f"\n{args.ticker} ticker events (anchor {anchor}): received={outcome.records_received} "
        f"written={outcome.records_written} skipped={outcome.records_skipped} "
        f"failed={outcome.records_failed}"
    )
    return _print_outcome(outcome)


def _cmd_listing_eras(args: argparse.Namespace, settings: Settings) -> int:
    if args.end < args.start:
        print(f"error: --end {args.end} is before --start {args.start}", file=sys.stderr)
        return EXIT_USAGE
    with get_session_factory(settings)() as session:
        resolution = HistoricalListingResolver(session).resolve_ticker_eras(
            args.ticker, args.start, args.end
        )
    print(
        f"{args.ticker} {args.start}..{args.end}: {resolution.status.value} -- {resolution.reason}"
    )
    if resolution.security_id is not None:
        print(f"  security: {resolution.security_id}")
    for first, last in resolution.ranges:
        print(f"  proven: {first} .. {last} (inclusive)")
    return EXIT_OK if resolution.status.value == "RESOLVED" else EXIT_RUN_FAILED


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
            _commit_failed_run(session)
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
                price_basis=request.price_basis,
                exchange_mic=request.exchange_mic,
            )
        except BaseException:
            _commit_failed_run(session)
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
                price_basis=request.price_basis,
                currency=request.currency,
            )
        except BaseException:
            _commit_failed_run(session)
            raise
        session.commit()
        return outcome


def _commit_failed_run(session: Session) -> None:
    """After a failed ingestion, commit the ``failed`` run row -- and only if it was recorded.

    The run recorder rolled the run's domain writes back to a savepoint and set
    ``FAILED_RUN_RECORDED`` once the failed row was in place. Without that
    marker (the failure happened before a run existed, or recording itself
    failed) nothing is committed: the session is rolled back, so an exception can
    never cause arbitrary pending state to be persisted. A failure while
    committing is logged and rolled back rather than replacing the original error.
    """
    if not session.info.pop(FAILED_RUN_RECORDED, False):
        session.rollback()
        return
    try:
        session.commit()
    except Exception:
        logger.exception("could not persist the failed ingestion run record")
        session.rollback()


def _sec_client(settings: Settings) -> SecClient:
    """Build the SEC adapter, failing clearly if no identity is configured."""
    config = SecConfig(
        user_agent=settings.sec_user_agent,
        base_url=settings.sec_base_url,
        request_timeout=settings.sec_request_timeout_seconds,
        max_retries=settings.sec_max_retries,
        min_request_interval=settings.sec_min_request_interval_seconds,
    )
    config.require_user_agent()
    return SecClient(config)


async def _ingest_sec_filings(
    *, settings: Settings, client: SecClient, cik: str, now: datetime
) -> RunOutcome:
    factory = get_session_factory(settings)
    with factory() as session:
        service = FilingIngestionService(
            session=session, provider=client, store=_store(settings), now=now
        )
        try:
            outcome = await service.ingest_filings(cik)
        except BaseException:
            _commit_failed_run(session)
            raise
        session.commit()
        return outcome


async def _ingest_stock_splits(  # noqa: PLR0913 - one keyword per request field
    *,
    settings: Settings,
    client: MassiveClient,
    ticker: str,
    start: date,
    end: date,
    now: datetime,
    provider_history_start: date | None,
) -> RunOutcome:
    factory = get_session_factory(settings)
    with factory() as session:
        service = SplitIngestionService(
            session=session, provider=client, store=_store(settings), now=now
        )
        try:
            outcome = await service.ingest_stock_splits(
                ticker, start, end, provider_history_start=provider_history_start
            )
        except BaseException:
            _commit_failed_run(session)
            raise
        session.commit()
        return outcome


async def _ingest_listing_history(
    *,
    settings: Settings,
    client: MassiveClient,
    now: datetime,
    operation: Callable[[ListingHistoryIngestionService], Awaitable[RunOutcome]],
) -> RunOutcome:
    factory = get_session_factory(settings)
    with factory() as session:
        service = ListingHistoryIngestionService(
            session=session, provider=client, store=_store(settings), now=now
        )
        try:
            outcome = await operation(service)
        except BaseException:
            _commit_failed_run(session)
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


def _classify(exc: Exception) -> int:  # noqa: PLR0911, PLR0912 - one return per distinct, named failure kind; splitting it up would not simplify anything
    """Map a failure onto an exit code and say why on stderr.

    Split out of :func:`main` so the ordering of the ``except`` clauses stays
    readable. Order matters: a bar conflict is a data decision, an auth failure is
    a configuration problem, and both are subclasses of the broader provider and
    lookup errors, so the specific cases have to be tried first.
    """
    if isinstance(
        exc,
        UnknownListingError
        | UnknownRegistrantError
        | InvalidHistoryRequestError
        | HistoryScopeError
        | InvalidFactRequestError
        | UnsupportedFormError
        | AccessionNotInPackageError
        | MissingSecFilingError
        | NothingToIngestError
        | FsdsPackageError
        | InvalidSplitRangeError
        | InvalidAnchorDateError
        | ListingHistoryConflictError
        | RetiredTickerCoverageError
        | AmbiguousSplitSecurityError
        | UnknownSplitTickerError,
    ):
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    if isinstance(exc, IncompleteListingHistoryError | ListingFactConflictError):
        print(f"error: {exc}", file=sys.stderr)
        print("nothing was stored; reconcile the source before re-running", file=sys.stderr)
        return EXIT_RUN_FAILED
    if isinstance(exc, IncompleteSplitFetchError | UnmappedSplitError):
        print(f"error: {exc}", file=sys.stderr)
        print("no coverage was recorded", file=sys.stderr)
        return EXIT_RUN_FAILED
    if isinstance(exc, StockSplitConflictError):
        print(f"error: {exc}", file=sys.stderr)
        print("nothing was overwritten; reconcile the source before re-running", file=sys.stderr)
        return EXIT_RUN_FAILED
    if isinstance(exc, RawStoreError):
        print(f"error: the raw artifact could not be read or preserved: {exc}", file=sys.stderr)
        print("nothing was stored by this run", file=sys.stderr)
        return EXIT_RUN_FAILED
    if isinstance(exc, PackageRevisionError):
        print(f"error: {exc}", file=sys.stderr)
        print(
            "nothing was overwritten; there is no package-revision workflow yet, so this "
            "package cannot be ingested",
            file=sys.stderr,
        )
        return EXIT_RUN_FAILED
    if isinstance(exc, AllFilingRecordsRejectedError):
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_RUN_FAILED
    if isinstance(
        exc,
        SecFilingConflictError
        | AccessionCikMismatchError
        | SubmissionMetadataMismatchError
        | FsdsRowsRejectedError
        | FinancialFactConflictError,
    ):
        print(f"error: {exc}", file=sys.stderr)
        print("nothing was overwritten; reconcile the source before re-running", file=sys.stderr)
        return EXIT_RUN_FAILED
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
