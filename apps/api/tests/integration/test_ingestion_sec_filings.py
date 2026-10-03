"""SEC filing ingestion against real PostgreSQL, with a stub provider (no SEC access).

Covers identity (accession per company), idempotency, conflicts, amendments as
separate rows, raw provenance, and the Step 10.4H failed-run guarantees: a failure
keeps a durable ``failed`` run and leaves no partial domain rows behind.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from quantforge.ingestion.canonical import CanonicalSecFiling
from quantforge.ingestion.filings import (
    AllFilingRecordsRejectedError,
    FilingIngestionService,
    SecFilingConflictError,
    UnknownRegistrantError,
)
from quantforge.ingestion.raw_store import RawResponseStore
from quantforge.models.company import Company
from quantforge.models.enums import IngestionStatus, ProviderType, SecFormType
from quantforge.models.filing import SecFiling
from quantforge.models.ingestion import IngestionRun
from quantforge.models.provenance import DataSource
from quantforge.providers.base import RawCapture
from quantforge.providers.errors import ProviderPayloadError
from quantforge.providers.filings import FilingsResult

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
CIK = "0000320193"


def _filing(**overrides: object) -> CanonicalSecFiling:
    fields: dict[str, object] = {
        "cik": CIK,
        "accession_number": "0000320193-24-000010",
        "form": SecFormType.FORM_10Q,
        "filing_date": date(2024, 5, 3),
        "accepted_at": datetime(2024, 5, 3, 20, 30, 22, tzinfo=UTC),
        "report_date": date(2024, 3, 30),
        "primary_document": "q.htm",
    }
    fields.update(overrides)
    return CanonicalSecFiling(**fields)  # type: ignore[arg-type]


class StubProvider:
    name = "sec-edgar"

    def __init__(
        self,
        filings: list[CanonicalSecFiling],
        *,
        rejected: tuple[str, ...] = (),
        error: Exception | None = None,
        body: bytes = b'{"filings":{"recent":{}}}',
    ) -> None:
        self._result = FilingsResult(
            filings=filings,
            rejected=rejected,
            captures=(
                RawCapture(
                    provider="sec-edgar",
                    dataset="submissions",
                    endpoint="/submissions/CIK{cik}.json",
                    request_parameters={"cik": CIK},
                    body=body,
                    retrieved_at=NOW,
                ),
            ),
        )
        self._error = error

    async def get_filings(self, cik: str) -> FilingsResult:
        if self._error is not None:
            raise self._error
        return self._result


@pytest.fixture
def company(db_session: Session) -> Company:
    row = Company(legal_name="Filing Test Inc.", display_name="Filing Test", cik=CIK)
    db_session.add(row)
    db_session.commit()
    return row


def _service(session: Session, provider: StubProvider, tmp_path: Path) -> FilingIngestionService:
    return FilingIngestionService(
        session=session,
        provider=provider,
        store=RawResponseStore(root=tmp_path / "raw"),
        now=NOW,
    )


async def _ingest(session: Session, provider: StubProvider, tmp_path: Path) -> object:
    outcome = await _service(session, provider, tmp_path).ingest_filings(CIK)
    session.commit()
    return outcome


@pytest.mark.anyio
async def test_filings_are_written_with_provenance(
    company: Company, db_session: Session, tmp_path: Path
) -> None:
    await _ingest(db_session, StubProvider([_filing()]), tmp_path)

    row = db_session.query(SecFiling).one()
    assert row.company_id == company.id
    assert row.accession_number == "0000320193-24-000010"
    assert row.form is SecFormType.FORM_10Q
    assert row.accepted_at == datetime(2024, 5, 3, 20, 30, 22, tzinfo=UTC)
    assert row.accepted_at != row.ingested_at, "acceptance time is never the ingestion time"
    assert row.ingested_at == NOW
    run = db_session.query(IngestionRun).one()
    assert row.ingestion_run_id == run.id
    assert (run.provider, run.dataset, run.status) == (
        "sec-edgar",
        "sec-filings",
        IngestionStatus.SUCCEEDED,
    )
    assert (run.records_received, run.records_written, run.records_skipped) == (1, 1, 0)
    source = db_session.query(DataSource).one()
    assert source.name == "sec-edgar" and row.source_id == source.id


@pytest.mark.anyio
async def test_raw_response_is_preserved_in_its_own_namespace(
    company: Company, db_session: Session, tmp_path: Path
) -> None:
    body = b'{"filings":{"recent":{"accessionNumber":["x"]}}}'
    await _ingest(db_session, StubProvider([_filing()], body=body), tmp_path)

    run = db_session.query(IngestionRun).one()
    assert run.raw_artifact_path and run.raw_artifact_path.startswith("sec-edgar/")
    manifest = json.loads((tmp_path / "raw" / run.raw_artifact_path).read_text())
    artifact = manifest["artifacts"][0]
    assert artifact["path"].startswith("sec-edgar/submissions/")
    assert (tmp_path / "raw" / artifact["path"]).read_bytes() == body
    assert not (tmp_path / "raw" / "massive").exists()
    assert not (tmp_path / "raw" / "finnhub").exists()


@pytest.mark.anyio
async def test_reingesting_identical_metadata_is_an_idempotent_skip(
    company: Company, db_session: Session, tmp_path: Path
) -> None:
    await _ingest(db_session, StubProvider([_filing()]), tmp_path)
    await _ingest(db_session, StubProvider([_filing()]), tmp_path)

    assert db_session.query(SecFiling).count() == 1
    # Both runs share the injected clock, so compare the pair rather than "the latest".
    tallies = sorted(
        (run.records_written, run.records_skipped) for run in db_session.query(IngestionRun)
    )
    assert tallies == [(0, 1), (1, 0)]


@pytest.mark.anyio
async def test_an_amendment_is_a_separate_row_and_the_original_is_untouched(
    company: Company, db_session: Session, tmp_path: Path
) -> None:
    original = _filing(form=SecFormType.FORM_10K, accession_number="0000320193-24-000020")
    amendment = _filing(
        form=SecFormType.FORM_10K_A,
        accession_number="0000320193-24-000021",
        filing_date=date(2024, 12, 2),
        accepted_at=datetime(2024, 12, 2, 14, 15, tzinfo=UTC),
    )
    await _ingest(db_session, StubProvider([original]), tmp_path)
    await _ingest(db_session, StubProvider([original, amendment]), tmp_path)

    rows = {r.accession_number: r for r in db_session.query(SecFiling).all()}
    assert set(rows) == {"0000320193-24-000020", "0000320193-24-000021"}
    assert rows["0000320193-24-000020"].form is SecFormType.FORM_10K
    assert rows["0000320193-24-000020"].accepted_at == original.accepted_at
    assert rows["0000320193-24-000021"].accepted_at == amendment.accepted_at


@pytest.mark.anyio
async def test_changed_source_facts_for_the_same_accession_conflict_and_nothing_is_overwritten(
    company: Company, db_session: Session, tmp_path: Path
) -> None:
    await _ingest(db_session, StubProvider([_filing()]), tmp_path)
    changed = _filing(
        filing_date=date(2024, 5, 6), accepted_at=datetime(2024, 5, 3, 21, 45, tzinfo=UTC)
    )

    with pytest.raises(SecFilingConflictError, match="filing_date") as caught:
        await _service(db_session, StubProvider([changed]), tmp_path).ingest_filings(CIK)
    db_session.commit()

    assert set(caught.value.differences) == {"filing_date", "accepted_at"}
    row = db_session.query(SecFiling).one()
    assert row.filing_date == date(2024, 5, 3), "the stored fact is never silently restated"
    failed = db_session.query(IngestionRun).filter_by(status=IngestionStatus.FAILED).one()
    assert failed.error_type == "SecFilingConflictError"


@pytest.mark.anyio
async def test_a_failure_keeps_a_durable_failed_run_and_no_partial_filings(
    company: Company, db_session: Session, tmp_path: Path
) -> None:
    await _ingest(db_session, StubProvider([_filing()]), tmp_path)
    existing = _filing()
    new_before_conflict = _filing(
        accession_number="0000320193-24-000005",
        accepted_at=datetime(2024, 4, 1, 15, 0, tzinfo=UTC),
        filing_date=date(2024, 4, 1),
    )
    conflicting = _filing(primary_document="other.htm")

    with pytest.raises(SecFilingConflictError):
        await _service(
            db_session, StubProvider([new_before_conflict, conflicting]), tmp_path
        ).ingest_filings(CIK)
    db_session.commit()  # what the CLI does when a failure was recorded

    assert {r.accession_number for r in db_session.query(SecFiling).all()} == {
        existing.accession_number
    }, "the earlier valid filing in the failed batch must roll back"
    failed = db_session.query(IngestionRun).filter_by(status=IngestionStatus.FAILED).one()
    assert failed.records_written == 0 and failed.raw_artifact_path


@pytest.mark.anyio
async def test_a_provider_failure_before_any_write_leaves_a_failed_run_and_no_rows(
    company: Company, db_session: Session, tmp_path: Path
) -> None:
    provider = StubProvider([], error=RuntimeError("SEC unreachable"))

    with pytest.raises(RuntimeError, match="unreachable"):
        await _service(db_session, provider, tmp_path).ingest_filings(CIK)
    db_session.commit()

    assert db_session.query(SecFiling).count() == 0
    assert db_session.query(DataSource).count() == 0, "created inside the failed block"
    assert db_session.query(IngestionRun).filter_by(status=IngestionStatus.FAILED).count() == 1


@pytest.mark.anyio
async def test_an_unknown_cik_is_an_explicit_failure_and_never_creates_a_company(
    db_session: Session, tmp_path: Path
) -> None:
    with pytest.raises(UnknownRegistrantError, match=CIK):
        await _service(db_session, StubProvider([_filing()]), tmp_path).ingest_filings(CIK)
    db_session.commit()

    assert db_session.query(Company).count() == 0
    assert db_session.query(SecFiling).count() == 0
    assert db_session.query(IngestionRun).filter_by(status=IngestionStatus.FAILED).count() == 1


@pytest.mark.anyio
async def test_rejected_records_make_the_run_partial_and_the_valid_ones_still_land(
    company: Company, db_session: Session, tmp_path: Path
) -> None:
    provider = StubProvider(
        [_filing()], rejected=("0000320193-24-000099 (8-K): no acceptanceDateTime",)
    )

    await _ingest(db_session, provider, tmp_path)

    run = db_session.query(IngestionRun).one()
    assert run.status is IngestionStatus.PARTIAL
    assert (run.records_received, run.records_written, run.records_failed) == (2, 1, 1)
    assert db_session.query(SecFiling).count() == 1


@pytest.mark.anyio
async def test_one_joint_filing_may_exist_under_two_registrants(
    company: Company, db_session: Session, tmp_path: Path
) -> None:
    other = Company(legal_name="Co-registrant Inc.", display_name="Co-registrant", cik="0000789019")
    db_session.add(other)
    db_session.commit()
    source = DataSource(name="sec-edgar-direct", provider_type=ProviderType.REGULATORY_FILING)
    db_session.add(source)
    db_session.commit()
    for owner in (company, other):
        db_session.add(
            SecFiling(
                company_id=owner.id,
                accession_number="0000320193-24-000010",
                form=SecFormType.FORM_8K,
                filing_date=date(2024, 5, 3),
                accepted_at=datetime(2024, 5, 3, 20, 30, tzinfo=UTC),
                source_id=source.id,
            )
        )
    db_session.commit()

    assert db_session.query(SecFiling).count() == 2


def test_the_database_enforces_accession_form_and_uniqueness(db_session: Session) -> None:
    company_row = Company(legal_name="Constraint Co", display_name="Constraint", cik="0000111111")
    source = DataSource(name="constraint-source", provider_type=ProviderType.REGULATORY_FILING)
    db_session.add_all([company_row, source])
    db_session.commit()

    def attempt(**overrides: object) -> None:
        fields: dict[str, object] = {
            "company_id": company_row.id,
            "accession_number": "0000111111-24-000001",
            "form": SecFormType.FORM_10Q,
            "filing_date": date(2024, 5, 3),
            "accepted_at": datetime(2024, 5, 3, 20, 0, tzinfo=UTC),
            "source_id": source.id,
        }
        fields.update(overrides)
        db_session.add(SecFiling(**fields))
        db_session.flush()

    attempt()
    with pytest.raises(sa.exc.IntegrityError, match="uq_sec_filing_company_accession"):
        attempt()
    db_session.rollback()
    company_row = db_session.merge(company_row)
    source = db_session.merge(source)

    with pytest.raises(sa.exc.IntegrityError, match="accession_number_format"):
        attempt(accession_number="000011111124000002")
    db_session.rollback()


@pytest.mark.anyio
async def test_a_run_where_every_record_is_rejected_is_a_failure_not_a_partial_success(
    company: Company, db_session: Session, tmp_path: Path
) -> None:
    provider = StubProvider(
        [],
        rejected=("0000320193-24-000098 (10-Q): bad time", "0000320193-24-000099 (8-K): bad time"),
    )

    with pytest.raises(AllFilingRecordsRejectedError, match="all 2"):
        await _service(db_session, provider, tmp_path).ingest_filings(CIK)
    db_session.commit()

    assert db_session.query(SecFiling).count() == 0
    failed = db_session.query(IngestionRun).filter_by(status=IngestionStatus.FAILED).one()
    assert failed.error_type == "AllFilingRecordsRejectedError"
    assert (failed.records_received, failed.records_failed, failed.records_written) == (2, 2, 0)
    manifest = json.loads((tmp_path / "raw" / (failed.raw_artifact_path or "")).read_text())
    assert "submissions-rejected" in manifest["artifacts"][0]["path"], "never an accepted artifact"
    assert (
        not list((tmp_path / "raw" / "sec-edgar" / "submissions").glob("**/*.json"))
        if (tmp_path / "raw" / "sec-edgar" / "submissions").exists()
        else True
    )


@pytest.mark.anyio
async def test_a_malformed_response_keeps_its_bytes_apart_from_accepted_artifacts(
    company: Company, db_session: Session, tmp_path: Path
) -> None:
    body = b'{"cik":"320193","filings":{}}'
    error = ProviderPayloadError("no 'filings.recent' object")
    error.captures = (
        RawCapture(
            provider="sec-edgar",
            dataset="submissions",
            endpoint="/submissions/CIK{cik}.json",
            request_parameters={"cik": CIK},
            body=body,
            retrieved_at=NOW,
        ),
    )

    with pytest.raises(ProviderPayloadError):
        await _service(db_session, StubProvider([], error=error), tmp_path).ingest_filings(CIK)
    db_session.commit()

    failed = db_session.query(IngestionRun).filter_by(status=IngestionStatus.FAILED).one()
    assert failed.raw_artifact_path
    manifest = json.loads((tmp_path / "raw" / failed.raw_artifact_path).read_text())
    path = manifest["artifacts"][0]["path"]
    assert "submissions-rejected" in path
    assert (tmp_path / "raw" / path).read_bytes() == body
    assert db_session.query(SecFiling).count() == 0
