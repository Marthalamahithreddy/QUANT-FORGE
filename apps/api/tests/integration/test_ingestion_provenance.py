"""Step 12C: provider response bytes that reached QuantForge are never silently discarded.

The invariant: IF a provider response successfully arrived, THEN its exact bytes stay auditable even
when decoding, validation, canonical mapping, one record of a batch, or the whole run fails. They end
either as accepted raw evidence of a normal ingestion, or as retained REJECTED evidence under the
``<dataset>-rejected`` namespace. Real clients over a mocked transport, the real services, real
PostgreSQL, a real raw store: only the network is faked.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from quantforge.ingestion.canonical import CanonicalEquityReference, ResolvedVenue
from quantforge.ingestion.raw_store import RawResponseStore
from quantforge.ingestion.reference import ReferenceResolver
from quantforge.ingestion.service import EarningsIngestionService, IngestionService
from quantforge.ingestion.sources import get_or_create_data_source
from quantforge.models.company import Company
from quantforge.models.earnings import EarningsEvent
from quantforge.models.enums import IngestionStatus
from quantforge.models.ingestion import IngestionRun
from quantforge.models.security import Listing
from quantforge.providers.base import RawCapture
from quantforge.providers.errors import ProviderError, ProviderPayloadError, ProviderResponseError
from quantforge.providers.finnhub.client import FinnhubClient, FinnhubConfig
from quantforge.providers.massive.client import MassiveClient, MassiveConfig

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
KEY = "test-key-not-real"
START, END = date(2026, 9, 1), date(2026, 9, 30)

AAPL: dict[str, Any] = {
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
MSFT: dict[str, Any] = dict(
    AAPL,
    ticker="MSFT",
    name="Microsoft Corporation",
    cik="0000789019",
    composite_figi="BBG000BPH459",
    share_class_figi="BBG001S5TD05",
)
UNDECODABLE = b"<html>upstream proxy error</html>"
NO_RESULTS = b'{"status":"OK","request_id":"r1"}'


def _body(results: dict[str, Any]) -> bytes:
    # Deliberately NOT json.dumps defaults: odd whitespace proves bytes are kept verbatim.
    return json.dumps({"status": "OK", "results": results}, indent=3).encode()


def _massive(handler: Callable[[httpx.Request], httpx.Response]) -> MassiveClient:
    config = MassiveConfig(api_key=KEY, base_url="https://api.massive.com", max_retries=0)
    return MassiveClient(config, transport=httpx.MockTransport(handler))


def _finnhub(handler: Callable[[httpx.Request], httpx.Response]) -> FinnhubClient:
    config = FinnhubConfig(api_key=KEY, base_url="https://finnhub.io/api/v1", max_retries=0)
    return FinnhubClient(config, transport=httpx.MockTransport(handler))


def _by_symbol(bodies: dict[str, bytes]) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        symbol = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, content=bodies[symbol])

    return handler


def _reference_service(
    session: Session, bodies: dict[str, bytes], raw: Path
) -> tuple[IngestionService, MassiveClient]:
    client = _massive(_by_symbol(bodies))
    return IngestionService(
        session=session, provider=client, store=RawResponseStore(raw), now=NOW
    ), client


def _run(session: Session, dataset: str) -> IngestionRun:
    session.flush()
    return session.scalars(
        sa.select(IngestionRun)
        .where(IngestionRun.dataset == dataset)
        .order_by(IngestionRun.started_at.desc(), IngestionRun.id)
    ).first()  # type: ignore[return-value]


def _manifest(raw: Path, run: IngestionRun) -> list[tuple[str, bytes]]:
    """``(relative path, exact bytes)`` of every artifact the run's manifest lists, verified."""
    assert run.raw_artifact_path, "the run must point at its evidence"
    manifest_bytes = (raw / run.raw_artifact_path).read_bytes()
    assert hashlib.sha256(manifest_bytes).hexdigest() == run.raw_content_sha256
    out: list[tuple[str, bytes]] = []
    for entry in json.loads(manifest_bytes)["artifacts"]:
        payload = (raw / entry["path"]).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == entry["sha256"]
        out.append((entry["path"], payload))
    return out


def _rejected(entries: list[tuple[str, bytes]], dataset: str) -> list[bytes]:
    return [body for path, body in entries if f"/{dataset}-rejected/" in f"/{path}"]


def _accepted(entries: list[tuple[str, bytes]], dataset: str) -> list[bytes]:
    return [body for path, body in entries if f"/{dataset}/" in f"/{path}"]


def _no_secret_on_disk(raw: Path) -> None:
    for path in raw.rglob("*"):
        if path.is_file():
            text = path.read_bytes().decode("utf-8", errors="replace")
            assert KEY not in text, path
            assert "Bearer" not in text, path


# ── reference ───────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_reference_success_keeps_the_exact_bytes_as_accepted_evidence(
    db_session: Session, tmp_path: Path
) -> None:
    body = _body(AAPL)
    service, client = _reference_service(db_session, {"AAPL": body}, tmp_path)
    async with client:
        outcome, _ = await service.ingest_reference(["AAPL"])

    run = _run(db_session, "reference")
    assert outcome.status is IngestionStatus.SUCCEEDED
    assert run.error_message is None and run.error_type is None
    entries = _manifest(tmp_path, run)
    assert _accepted(entries, "reference") == [body]
    assert _rejected(entries, "reference") == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("label", "body"),
    [
        ("undecodable 200", UNDECODABLE),
        ("a document with no results", NO_RESULTS),
        ("results of the wrong shape", b'{"status":"OK","results":[1,2]}'),
        ("a DTO that fails validation", _body(dict(AAPL, active="not-a-bool"))),
        ("a pathologically nested body", b"[" * 100_000 + b"]" * 100_000),
    ],
)
async def test_reference_response_the_client_refuses_is_kept_as_rejected_bytes(
    db_session: Session, tmp_path: Path, label: str, body: bytes
) -> None:
    service, client = _reference_service(db_session, {"AAPL": body}, tmp_path)
    async with client:
        outcome, _ = await service.ingest_reference(["AAPL"])

    run = _run(db_session, "reference")
    assert outcome.status is IngestionStatus.PARTIAL, label
    assert (run.records_received, run.records_written, run.records_failed) == (1, 0, 1)
    entries = _manifest(tmp_path, run)
    assert _rejected(entries, "reference") == [body], label
    assert _accepted(entries, "reference") == [], "a refused response is never filed as accepted"
    assert db_session.scalar(sa.select(sa.func.count()).select_from(Company)) == 0
    _no_secret_on_disk(tmp_path)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("label", "payload"),
    [
        ("non-US locale (canonical mapping refuses)", dict(AAPL, locale="global")),
        (
            "no stable identifier (resolution refuses)",
            dict(AAPL, cik=None, composite_figi=None, share_class_figi=None, figi=None),
        ),
    ],
)
async def test_reference_canonical_or_resolution_failure_after_a_good_response_is_rejected_evidence(
    db_session: Session, tmp_path: Path, label: str, payload: dict[str, Any]
) -> None:
    body = _body(payload)
    service, client = _reference_service(db_session, {"AAPL": body}, tmp_path)
    async with client:
        outcome, _ = await service.ingest_reference(["AAPL"])

    run = _run(db_session, "reference")
    assert outcome.status is IngestionStatus.PARTIAL, label
    entries = _manifest(tmp_path, run)
    assert _rejected(entries, "reference") == [body], label
    assert _accepted(entries, "reference") == [], "bytes we refused to ingest are not accepted"


@pytest.mark.anyio
async def test_reference_one_ticker_fails_while_the_other_succeeds(
    db_session: Session, tmp_path: Path
) -> None:
    good, bad = _body(AAPL), UNDECODABLE
    service, client = _reference_service(db_session, {"AAPL": good, "MSFT": bad}, tmp_path)
    async with client:
        outcome, results = await service.ingest_reference(["AAPL", "MSFT"])

    run = _run(db_session, "reference")
    assert outcome.status is IngestionStatus.PARTIAL, "per-ticker semantics are preserved"
    assert [r.ticker for r in results] == ["AAPL"]
    assert (run.records_received, run.records_written, run.records_failed) == (2, 1, 1)
    entries = _manifest(tmp_path, run)
    assert _accepted(entries, "reference") == [good]
    assert _rejected(entries, "reference") == [bad]
    assert db_session.scalar(sa.select(sa.func.count()).select_from(Company)) == 1


@pytest.mark.anyio
async def test_reference_evidence_already_fetched_survives_a_later_unexpected_failure(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Incremental retention: ticker 1 is obtained and resolved, ticker 2's resolution blows up
    unexpectedly and fails the WHOLE run; neither response's bytes may be lost."""
    first, second = _body(AAPL), _body(MSFT)
    service, client = _reference_service(db_session, {"AAPL": first, "MSFT": second}, tmp_path)
    real = ReferenceResolver.resolve
    calls: list[str] = []

    def explode_on_the_second(self: ReferenceResolver, reference: Any, **kwargs: Any) -> Any:
        calls.append(reference.ticker)
        if reference.ticker == "MSFT":
            message = "unexpected storage failure"
            raise RuntimeError(message)
        return real(self, reference, **kwargs)

    monkeypatch.setattr(ReferenceResolver, "resolve", explode_on_the_second)
    async with client:
        with pytest.raises(RuntimeError, match="unexpected storage failure"):
            await service.ingest_reference(["AAPL", "MSFT"])

    run = _run(db_session, "reference")
    assert run.status is IngestionStatus.FAILED
    assert run.records_written == 0, "the failed run's domain writes rolled back"
    entries = _manifest(tmp_path, run)
    assert _accepted(entries, "reference") == [first], "ticker 1's bytes were not discarded"
    assert _rejected(entries, "reference") == [second], "ticker 2's response was refused"


@pytest.mark.anyio
async def test_reference_retry_after_a_partial_run_is_safe_and_deduplicates(
    db_session: Session, tmp_path: Path
) -> None:
    good, bad, fixed = _body(AAPL), UNDECODABLE, _body(MSFT)
    first_service, first_client = _reference_service(
        db_session, {"AAPL": good, "MSFT": bad}, tmp_path
    )
    async with first_client:
        await first_service.ingest_reference(["AAPL", "MSFT"])
    db_session.commit()

    second_service, second_client = _reference_service(
        db_session, {"AAPL": good, "MSFT": fixed}, tmp_path
    )
    async with second_client:
        outcome, _ = await second_service.ingest_reference(["AAPL", "MSFT"])
    db_session.commit()

    assert outcome.status is IngestionStatus.SUCCEEDED
    assert (outcome.records_written, outcome.records_skipped, outcome.records_failed) == (1, 1, 0)
    assert db_session.scalar(sa.select(sa.func.count()).select_from(Company)) == 2
    assert db_session.scalar(sa.select(sa.func.count()).select_from(Listing)) == 2
    runs = {run.status: run for run in db_session.scalars(sa.select(IngestionRun))}
    assert set(runs) == {IngestionStatus.PARTIAL, IngestionStatus.SUCCEEDED}
    retry = runs[IngestionStatus.SUCCEEDED]
    assert retry.error_message is None
    entries = _manifest(tmp_path, retry)
    assert _rejected(entries, "reference") == [], "the retry's manifest lists no rejected bytes"
    assert _accepted(entries, "reference") == [good, fixed]
    rows = json.loads((tmp_path / (retry.raw_artifact_path or "")).read_bytes())["artifacts"]
    assert rows[0]["deduplicated"] is True, (
        "the unchanged AAPL body is the same content-addressed file"
    )


# ── earnings ────────────────────────────────────────────────────────────────

CALENDAR_OK = (
    b'{"earningsCalendar": [{"symbol": "AAPL", "date": "2026-09-14", "year": 2026, '
    b'"quarter": 3, "epsActual": 1.46, "epsEstimate": 1.4, "hour": "amc"}]}'
)


def _calendar(*entries: dict[str, Any]) -> bytes:
    return json.dumps({"earningsCalendar": list(entries)}, indent=2).encode()


def _entry(symbol: str = "AAPL", **overrides: Any) -> dict[str, Any]:
    entry = {"symbol": symbol, "date": "2026-09-14", "year": 2026, "quarter": 3, "hour": "amc"}
    entry.update(overrides)
    return entry


@pytest.fixture
def apple(db_session: Session) -> None:
    """AAPL's reference is stored, so earnings can resolve its issuer."""
    source = get_or_create_data_source(db_session, name="finnhub", base_url=None)
    ReferenceResolver(db_session).resolve(
        CanonicalEquityReference(
            ticker="AAPL",
            security_type="EQUITY",
            venue=ResolvedVenue(mic="XNAS", name="Nasdaq", timezone_name="America/New_York"),
            company_name="Apple Inc.",
            cik="0000320193",
            composite_figi="BBG000B9XRY4",
            share_class_figi="BBG001S5QY8",
            currency_code="USD",
            is_active=True,
        ),
        source_id=source.id,
        as_of=date(2026, 9, 1),
    )
    db_session.commit()


def _earnings_service(
    session: Session, body: bytes, raw: Path
) -> tuple[EarningsIngestionService, FinnhubClient]:
    client = _finnhub(lambda _request: httpx.Response(200, content=body))
    return EarningsIngestionService(
        session=session, provider=client, store=RawResponseStore(raw), now=NOW
    ), client


@pytest.mark.anyio
@pytest.mark.usefixtures("apple")
async def test_earnings_success_keeps_the_exact_bytes_as_accepted_evidence(
    db_session: Session, tmp_path: Path
) -> None:
    service, client = _earnings_service(db_session, CALENDAR_OK, tmp_path / "raw")
    async with client:
        outcome = await service.ingest_earnings("AAPL", START, END)

    run = _run(db_session, "earnings")
    assert outcome.status is IngestionStatus.SUCCEEDED and outcome.records_written == 1
    assert run.error_message is None and run.error_type is None
    entries = _manifest(tmp_path / "raw", run)
    assert _accepted(entries, "earnings") == [CALENDAR_OK]
    assert _rejected(entries, "earnings") == []


@pytest.mark.anyio
@pytest.mark.usefixtures("apple")
@pytest.mark.parametrize(
    ("label", "body", "error"),
    [
        ("undecodable 200", UNDECODABLE, ProviderResponseError),
        ("top level is not an object", b"[1, 2, 3]", ProviderResponseError),
        ("calendar of the wrong shape", b'{"earningsCalendar": "nope"}', ProviderResponseError),
        (
            "an entry with no date",
            _calendar({"symbol": "AAPL", "year": 2026}),
            ProviderResponseError,
        ),
        (
            "canonical mapping refuses (quarter 9)",
            _calendar(_entry(quarter=9)),
            ProviderPayloadError,
        ),
        (
            "a pathologically nested body",
            b"[" * 100_000 + b"]" * 100_000,
            ProviderResponseError,
        ),
    ],
)
async def test_earnings_response_the_adapter_refuses_is_kept_as_rejected_bytes(
    db_session: Session, tmp_path: Path, label: str, body: bytes, error: type[ProviderError]
) -> None:
    raw = tmp_path / "raw"
    service, client = _earnings_service(db_session, body, raw)
    async with client:
        with pytest.raises(error):
            await service.ingest_earnings("AAPL", START, END)

    run = _run(db_session, "earnings")
    assert run.status is IngestionStatus.FAILED, label
    assert run.records_written == 0
    entries = _manifest(raw, run)
    assert _rejected(entries, "earnings") == [body], label
    assert _accepted(entries, "earnings") == []
    assert db_session.scalar(sa.select(sa.func.count()).select_from(EarningsEvent)) == 0
    _no_secret_on_disk(raw)


@pytest.mark.anyio
@pytest.mark.usefixtures("apple")
async def test_earnings_partial_event_failure_keeps_accepted_bytes_and_stays_partial(
    db_session: Session, tmp_path: Path
) -> None:
    raw = tmp_path / "raw"
    body = _calendar(_entry("AAPL"), _entry("ZZZZ", date="2026-09-15"))
    service, client = _earnings_service(db_session, body, raw)
    async with client:
        outcome = await service.ingest_earnings("AAPL", START, END)

    run = _run(db_session, "earnings")
    assert outcome.status is IngestionStatus.PARTIAL
    assert (run.records_received, run.records_written, run.records_failed) == (2, 1, 1)
    entries = _manifest(raw, run)
    assert _accepted(entries, "earnings") == [body], "the response itself was valid"
    assert _rejected(entries, "earnings") == []


@pytest.mark.anyio
@pytest.mark.usefixtures("apple")
async def test_earnings_retry_after_a_failure_is_safe_and_not_duplicated(
    db_session: Session, tmp_path: Path
) -> None:
    raw = tmp_path / "raw"
    bad_service, bad_client = _earnings_service(db_session, UNDECODABLE, raw)
    async with bad_client:
        with pytest.raises(ProviderResponseError):
            await bad_service.ingest_earnings("AAPL", START, END)
    db_session.commit()

    good_service, good_client = _earnings_service(db_session, CALENDAR_OK, raw)
    async with good_client:
        first = await good_service.ingest_earnings("AAPL", START, END)
        db_session.commit()
        again = await good_service.ingest_earnings("AAPL", START, END)
        db_session.commit()

    assert (first.records_written, first.records_skipped) == (1, 0)
    assert (again.records_written, again.records_skipped) == (0, 1)
    assert db_session.scalar(sa.select(sa.func.count()).select_from(EarningsEvent)) == 1
    statuses = list(
        db_session.scalars(
            sa.select(IngestionRun.status)
            .where(IngestionRun.dataset == "earnings")
            .order_by(IngestionRun.started_at, IngestionRun.id)
        )
    )
    assert statuses.count(IngestionStatus.FAILED) == 1
    assert statuses.count(IngestionStatus.SUCCEEDED) == 2


# ── partial-run reasons, through the services ───────────────────────────────


@pytest.mark.anyio
async def test_reference_partial_run_says_why_with_the_ticker(
    db_session: Session, tmp_path: Path
) -> None:
    service, client = _reference_service(
        db_session, {"AAPL": _body(AAPL), "MSFT": _body(dict(MSFT, locale="global"))}, tmp_path
    )
    async with client:
        await service.ingest_reference(["AAPL", "MSFT"])

    run = _run(db_session, "reference")
    assert run.status is IngestionStatus.PARTIAL
    assert run.error_type == "ProviderPayloadError"
    assert (run.error_message or "").startswith("1 record(s) failed; first: MSFT: ")
    assert "locale 'global' is not 'us'" in (run.error_message or "")


@pytest.mark.anyio
async def test_reference_several_failures_report_the_count_and_the_first_in_request_order(
    db_session: Session, tmp_path: Path
) -> None:
    bodies = {"AAPL": UNDECODABLE, "MSFT": NO_RESULTS, "ORCL": _body(AAPL)}
    service, client = _reference_service(db_session, bodies, tmp_path)
    async with client:
        await service.ingest_reference(["AAPL", "MSFT", "ORCL"])

    run = _run(db_session, "reference")
    assert run.records_failed == 2
    assert (run.error_message or "").startswith("2 record(s) failed; first: AAPL: ")


@pytest.mark.anyio
async def test_a_reason_never_carries_a_credential_a_dsn_a_contact_or_a_path(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    leaky = (
        "AAPL: refused token=abc123SECRET via postgresql://qf:Hunter2pw@db.internal/qf "
        "contact jane.doe@example.org at /Users/someone/raw/x.json"
    )

    def refuse(self: IngestionService, view: Any, ticker: str) -> Any:
        raise ProviderPayloadError(leaky, identifier=ticker)

    monkeypatch.setattr(IngestionService, "_to_canonical", refuse)
    service, client = _reference_service(db_session, {"AAPL": _body(AAPL)}, tmp_path)
    async with client:
        await service.ingest_reference(["AAPL"])

    message = _run(db_session, "reference").error_message or ""
    assert message.startswith("1 record(s) failed; first: AAPL: ")
    for secret in ("abc123SECRET", "Hunter2pw", "jane.doe@example.org", "/Users/someone", KEY):
        assert secret not in message


@pytest.mark.anyio
@pytest.mark.usefixtures("apple")
async def test_earnings_partial_run_says_which_event_failed_and_why(
    db_session: Session, tmp_path: Path
) -> None:
    body = _calendar(
        _entry("AAPL"), _entry("ZZZZ", date="2026-09-15"), _entry("YYYY", date="2026-09-16")
    )
    service, client = _earnings_service(db_session, body, tmp_path / "raw")
    async with client:
        await service.ingest_earnings("AAPL", START, END)

    run = _run(db_session, "earnings")
    assert run.status is IngestionStatus.PARTIAL and run.records_failed == 2
    assert run.error_type == "UnknownIssuerError"
    # Events are processed in (ticker, date) order, so the first failure is YYYY, deterministically.
    assert (run.error_message or "").startswith("2 record(s) failed; first: YYYY 2026-09-16: ")
    assert "YYYY is not stored" in (run.error_message or "")


@pytest.mark.anyio
@pytest.mark.usefixtures("apple")
async def test_a_successful_earnings_run_has_no_fabricated_error(
    db_session: Session, tmp_path: Path
) -> None:
    service, client = _earnings_service(db_session, CALENDAR_OK, tmp_path / "raw")
    async with client:
        await service.ingest_earnings("AAPL", START, END)
    run = _run(db_session, "earnings")
    assert run.status is IngestionStatus.SUCCEEDED
    assert (run.error_type, run.error_message) == (None, None)


@pytest.mark.anyio
@pytest.mark.usefixtures("apple")
async def test_a_failed_earnings_run_keeps_the_exception_text_it_always_had(
    db_session: Session, tmp_path: Path
) -> None:
    service, client = _earnings_service(db_session, UNDECODABLE, tmp_path / "raw")
    async with client:
        with pytest.raises(ProviderResponseError):
            await service.ingest_earnings("AAPL", START, END)
    run = _run(db_session, "earnings")
    assert run.status is IngestionStatus.FAILED
    assert run.error_type == "ProviderResponseError"
    assert (run.error_message or "").startswith("malformed provider response: ")


# ── namespaces ──────────────────────────────────────────────────────────────


def test_identical_bytes_kept_as_accepted_and_as_rejected_are_two_distinct_artifacts(
    tmp_path: Path,
) -> None:
    store = RawResponseStore(tmp_path)
    capture = RawCapture(
        provider="massive",
        dataset="reference",
        endpoint="/v3/reference/tickers/{ticker}",
        request_parameters={"ticker": "AAPL"},
        body=b'{"same":"bytes"}',
        retrieved_at=NOW,
        provider_status=None,
    )
    (accepted,) = store.store_all([capture])
    (rejected,) = store.store_all([replace(capture, dataset="reference-rejected")])

    assert accepted.artifact.relative_path != rejected.artifact.relative_path
    assert "/reference/" in f"/{accepted.artifact.relative_path}"
    assert "/reference-rejected/" in f"/{rejected.artifact.relative_path}"
    assert rejected.artifact.deduplicated is False, "never deduplicated onto an accepted artifact"
    assert accepted.artifact.sha256 == rejected.artifact.sha256
