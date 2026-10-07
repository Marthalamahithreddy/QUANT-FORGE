"""On-demand, accession-scoped EDGAR-header enrichment of ``SecFiling.accepted_at`` (step 11.2H)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session
from tests.sec_header_fixture import header

from quantforge.ingestion.canonical import CanonicalSecFiling
from quantforge.ingestion.filing_acceptance import (
    AcceptanceHeaderMismatchError,
    AcceptanceStatus,
    FilingAcceptanceService,
    FilingNotStoredError,
    InvalidAcceptanceRequestError,
    SecAcceptanceConflictError,
)
from quantforge.ingestion.filings import FilingIngestionService
from quantforge.ingestion.raw_store import RawResponseStore
from quantforge.models.company import Company
from quantforge.models.enums import IngestionStatus, SecFormType
from quantforge.models.filing import SecFiling
from quantforge.models.ingestion import IngestionRun
from quantforge.providers.base import RawCapture
from quantforge.providers.errors import ProviderNotFoundError, ProviderPayloadError
from quantforge.providers.filings import FilingHeaderResult, FilingsResult
from quantforge.providers.sec.header import parse_filing_header

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
CIK = "0000320187"
TARGET = "0000320187-26-000037"
OTHER = "0000320187-26-000193"
ESTABLISHED = datetime(2026, 4, 1, 20, 16, 39, tzinfo=UTC)


def _filing(
    accession: str, filing_date: date, form: SecFormType = SecFormType.FORM_10Q
) -> CanonicalSecFiling:
    return CanonicalSecFiling(
        cik=CIK,
        accession_number=accession,
        form=form,
        filing_date=filing_date,
        report_date=date(2026, 2, 28),
        primary_document="nke-20260228.htm",
    )


class _BulkProvider:
    name = "sec-edgar"

    def __init__(self, filings: list[CanonicalSecFiling]) -> None:
        self._filings = filings

    async def get_filings(self, cik: str) -> FilingsResult:
        capture = RawCapture(
            provider="sec-edgar",
            dataset="submissions",
            endpoint="/submissions/CIK{cik}.json",
            request_parameters={"cik": cik},
            body=b"{}",
            retrieved_at=NOW,
        )
        return FilingsResult(filings=self._filings, rejected=(), captures=(capture,))


class _HeaderProvider:
    """Stands in for the SEC client: returns a scripted header body, counting requests."""

    name = "sec-edgar"

    def __init__(self, body: bytes | None = None, error: Exception | None = None) -> None:
        self.body = body
        self.error = error
        self.calls: list[tuple[str, str]] = []

    async def get_filing_header(self, cik: str, accession_number: str) -> FilingHeaderResult:
        self.calls.append((cik, accession_number))
        if self.error is not None:
            raise self.error
        assert self.body is not None
        capture = RawCapture(
            provider="sec-edgar",
            dataset="filing-header",
            endpoint="/Archives/edgar/data/{cik}/{accession_without_dashes}/{accession}.hdr.sgml",
            request_parameters={"cik": cik, "accession": accession_number},
            body=self.body,
            retrieved_at=NOW,
        )
        try:
            parsed = parse_filing_header(self.body)
        except ProviderPayloadError as exc:
            exc.captures = (capture,)
            raise
        return FilingHeaderResult(header=parsed, capture=capture)


@pytest.fixture
def store(tmp_path: Path) -> RawResponseStore:
    return RawResponseStore(tmp_path / "raw")


@pytest.fixture
async def stored(db_session: Session, store: RawResponseStore) -> None:
    company = Company(legal_name="Example Filer Inc.", display_name="Example Filer", cik=CIK)
    db_session.add(company)
    db_session.commit()
    filings = [
        _filing(TARGET, date(2026, 4, 1)),
        _filing(OTHER, date(2026, 10, 2)),
        _filing("0000320187-26-000184", date(2026, 10, 1), SecFormType.FORM_8K),
    ]
    await FilingIngestionService(
        session=db_session, provider=_BulkProvider(filings), store=store, now=NOW
    ).ingest_filings(CIK)
    db_session.commit()


def _service(
    db_session: Session, provider: _HeaderProvider, store: RawResponseStore
) -> FilingAcceptanceService:
    return FilingAcceptanceService(session=db_session, provider=provider, store=store, now=NOW)


def _accepted(db_session: Session, accession: str = TARGET) -> datetime | None:
    return db_session.scalar(
        sa.select(SecFiling.accepted_at).where(SecFiling.accession_number == accession)
    )


@pytest.mark.anyio
async def test_bulk_identity_ingestion_leaves_every_acceptance_instant_null(
    db_session: Session, stored: None
) -> None:
    rows = db_session.scalars(sa.select(SecFiling)).all()
    assert len(rows) == 3
    assert all(row.accepted_at is None for row in rows)


@pytest.mark.anyio
async def test_the_header_sets_accepted_at_null_to_value_with_raw_provenance(
    db_session: Session, store: RawResponseStore, stored: None
) -> None:
    provider = _HeaderProvider(header())

    report = await _service(db_session, provider, store).enrich(TARGET)
    db_session.commit()

    assert report.status is AcceptanceStatus.ESTABLISHED and report.source == "EDGAR_HEADER"
    assert report.accepted_at == ESTABLISHED
    assert _accepted(db_session) == ESTABLISHED
    assert provider.calls == [(CIK, TARGET)], "exactly one request"
    assert _accepted(db_session, OTHER) is None, "no fan-out to other filings"
    run = db_session.scalars(
        sa.select(IngestionRun).where(IngestionRun.dataset == "sec-filing-acceptance")
    ).one()
    assert run.status is IngestionStatus.SUCCEEDED and run.records_written == 1
    assert run.request_parameters == {"accession": TARGET, "cik": CIK, "mode": "establish"}
    assert run.raw_artifact_path and run.raw_content_sha256
    manifest = json.loads(store.read(run.raw_artifact_path))
    stored_paths = [item["path"] for item in manifest["artifacts"]]
    assert len(stored_paths) == 1 and "filing-header" in stored_paths[0]
    assert (store.root / stored_paths[0]).read_bytes() == header(), "verbatim header bytes"


@pytest.mark.anyio
async def test_an_established_instant_is_returned_without_another_request(
    db_session: Session, store: RawResponseStore, stored: None
) -> None:
    provider = _HeaderProvider(header())
    await _service(db_session, provider, store).enrich(TARGET)
    db_session.commit()
    runs_before = db_session.query(IngestionRun).count()

    again = await _service(db_session, provider, store).enrich(TARGET)

    assert again.status is AcceptanceStatus.ALREADY_ESTABLISHED and again.outcome is None
    assert again.source == "STORED_VALUE", (
        "no header was fetched, so no header provenance is claimed"
    )
    assert again.accepted_at == ESTABLISHED
    assert len(provider.calls) == 1, "no second request"
    assert db_session.query(IngestionRun).count() == runs_before


@pytest.mark.anyio
async def test_verify_mode_re_fetches_and_confirms_an_equal_instant(
    db_session: Session, store: RawResponseStore, stored: None
) -> None:
    provider = _HeaderProvider(header())
    await _service(db_session, provider, store).enrich(TARGET)
    db_session.commit()

    report = await _service(db_session, provider, store).enrich(TARGET, verify=True)
    db_session.commit()

    assert report.status is AcceptanceStatus.VERIFIED
    assert len(provider.calls) == 2
    assert _accepted(db_session) == ESTABLISHED


@pytest.mark.anyio
async def test_a_disagreeing_header_is_a_hard_conflict_that_overwrites_nothing(
    db_session: Session, store: RawResponseStore, stored: None
) -> None:
    await _service(db_session, _HeaderProvider(header()), store).enrich(TARGET)
    db_session.commit()
    different = _HeaderProvider(header(acceptance="20260401171639"))

    with pytest.raises(SecAcceptanceConflictError) as caught:
        await _service(db_session, different, store).enrich(TARGET, verify=True)
    db_session.commit()

    assert caught.value.stored == ESTABLISHED
    assert _accepted(db_session) == ESTABLISHED
    failed = db_session.scalars(
        sa.select(IngestionRun).where(IngestionRun.status == IngestionStatus.FAILED)
    ).one()
    assert failed.error_type == "SecAcceptanceConflictError"
    assert failed.raw_artifact_path is not None
    manifest = json.loads(store.read(failed.raw_artifact_path))
    assert "filing-header-rejected" in manifest["artifacts"][0]["path"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("override", "field"),
    [
        ({"accession": OTHER}, "accession"),
        ({"form": "10-K"}, "form"),
        ({"filing_date": "20260402"}, "filing date"),
        ({"ciks": ("0000789019",)}, "CIK"),
    ],
)
async def test_a_200_that_does_not_describe_the_stored_filing_changes_nothing(
    db_session: Session,
    store: RawResponseStore,
    stored: None,
    override: dict[str, object],
    field: str,
) -> None:
    provider = _HeaderProvider(header(**override))  # type: ignore[arg-type]

    with pytest.raises(AcceptanceHeaderMismatchError, match=field):
        await _service(db_session, provider, store).enrich(TARGET)
    db_session.commit()

    assert _accepted(db_session) is None
    failed = db_session.scalars(
        sa.select(IngestionRun).where(IngestionRun.status == IngestionStatus.FAILED)
    ).one()
    assert failed.raw_artifact_path, "the mismatching response is kept as rejected evidence"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "error_or_body",
    [
        ProviderNotFoundError("x", "no header"),
        b"<html>maintenance</html>",
        header(acceptance="20251102013000", filing_date="20260401"),
        header(acceptance=""),
    ],
)
async def test_any_failure_leaves_accepted_at_null_with_no_fallback(
    db_session: Session, store: RawResponseStore, stored: None, error_or_body: object
) -> None:
    provider = (
        _HeaderProvider(error=error_or_body)
        if isinstance(error_or_body, Exception)
        else _HeaderProvider(error_or_body)  # type: ignore[arg-type]
    )

    with pytest.raises((ProviderNotFoundError, ProviderPayloadError)):
        await _service(db_session, provider, store).enrich(TARGET)
    db_session.commit()

    assert _accepted(db_session) is None
    assert len(provider.calls) == 1


@pytest.mark.anyio
async def test_an_unknown_or_malformed_request_makes_no_request(
    db_session: Session, store: RawResponseStore, stored: None
) -> None:
    provider = _HeaderProvider(header())
    service = _service(db_session, provider, store)

    with pytest.raises(FilingNotStoredError):
        await service.enrich("0000320187-26-999999")
    with pytest.raises(InvalidAcceptanceRequestError):
        await service.enrich("not-an-accession")
    with pytest.raises(InvalidAcceptanceRequestError):
        await service.enrich(TARGET, cik="abc")
    with pytest.raises(FilingNotStoredError):
        await service.enrich(TARGET, cik="0000789019")

    assert provider.calls == []


@pytest.mark.anyio
async def test_bulk_reingestion_preserves_a_header_established_instant(
    db_session: Session, store: RawResponseStore, stored: None
) -> None:
    await _service(db_session, _HeaderProvider(header()), store).enrich(TARGET)
    db_session.commit()

    outcome = await FilingIngestionService(
        session=db_session,
        provider=_BulkProvider(
            [
                _filing(TARGET, date(2026, 4, 1)),
                _filing(OTHER, date(2026, 10, 2)),
                _filing("0000320187-26-000184", date(2026, 10, 1), SecFormType.FORM_8K),
            ]
        ),
        store=store,
        now=NOW,
    ).ingest_filings(CIK)
    db_session.commit()

    assert (outcome.records_written, outcome.records_skipped) == (0, 3)
    assert _accepted(db_session) == ESTABLISHED, "bulk identity never erases stronger evidence"
    assert _accepted(db_session, OTHER) is None


class _RacingProvider(_HeaderProvider):
    """Another writer establishes the instant while this call is waiting on the SEC."""

    def __init__(self, body: bytes, winner: datetime, session: Session) -> None:
        super().__init__(body)
        self._winner = winner
        self._session = session

    async def get_filing_header(self, cik: str, accession_number: str) -> FilingHeaderResult:
        self._session.execute(
            sa.update(SecFiling)
            .where(SecFiling.accession_number == accession_number)
            .values(accepted_at=self._winner)
        )
        return await super().get_filing_header(cik, accession_number)


@pytest.mark.anyio
async def test_losing_a_race_to_an_equal_instant_is_reported_as_verified(
    db_session: Session, store: RawResponseStore, stored: None
) -> None:
    provider = _RacingProvider(header(), ESTABLISHED, db_session)

    report = await _service(db_session, provider, store).enrich(TARGET)
    db_session.commit()

    assert report.status is AcceptanceStatus.VERIFIED
    assert _accepted(db_session) == ESTABLISHED


@pytest.mark.anyio
async def test_losing_a_race_to_a_different_instant_is_a_hard_conflict(
    db_session: Session, store: RawResponseStore, stored: None
) -> None:
    other = datetime(2026, 4, 1, 21, 16, 39, tzinfo=UTC)
    provider = _RacingProvider(header(), other, db_session)

    with pytest.raises(SecAcceptanceConflictError):
        await _service(db_session, provider, store).enrich(TARGET)
    # (The other writer here shares this session, so the failed run rolls its write back; a real
    # concurrent writer commits independently. What matters is the refusal, not an overwrite.)
