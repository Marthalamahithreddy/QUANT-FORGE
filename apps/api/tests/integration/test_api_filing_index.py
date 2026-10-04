"""``GET /api/v1/filings`` over real stored rows, via real HTTP (TestClient).

Rows are inserted directly (companies, filings, artifacts, submissions) so every case controls the
exact logical filings, their submission counts and periods. Nothing is ingested or fetched.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from quantforge.api.v1 import dependencies
from quantforge.core.config import Settings
from quantforge.db.session import get_session
from quantforge.ingestion.sources import get_or_create_data_source
from quantforge.main import create_app
from quantforge.models.company import Company
from quantforge.models.enums import FinancialFactSourceKind, ProviderType, SecFormType
from quantforge.models.filing import SecFiling
from quantforge.models.financial_fact import FinancialFactArtifact, FinancialFactSubmission
from quantforge.repositories.financial_fact import FinancialFactRepository

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
CIK_A = "0000320193"
CIK_B = "0000555555"
FORBIDDEN_KEYS = {
    "accepted_at",
    "source_accepted",
    "ingested_at",
    "observed_at",
    "available_at",
    "known_as_of",
    "tradable",
    "created_at",
    "id",
    "ingestion_run_id",
    "raw_artifact_path",
    "run_id",
    "ticker",
    "company_name",
    "exchange",
    "value",
}
ITEM_KEYS = {"accession", "company_cik", "form", "filing_date", "report_date", "fsds_period_end"}

Seed = Callable[..., SecFiling]


@pytest.fixture
def client(db_session: Session) -> Iterator[TestClient]:
    application: FastAPI = create_app(Settings(_env_file=None))
    application.dependency_overrides[get_session] = lambda: db_session
    with TestClient(application) as test_client:
        yield test_client


@pytest.fixture
def seed(db_session: Session) -> Seed:
    source = get_or_create_data_source(
        db_session, name="sec-edgar", provider_type=ProviderType.REGULATORY_FILING
    )
    companies: dict[str, Company] = {}
    artifacts: list[FinancialFactArtifact] = []

    def company(cik: str | None, key: str | None = None) -> Company:
        key = key or cik or "none"
        if key not in companies:
            row = Company(legal_name=f"Co {key}", display_name=f"Co {key}", cik=cik)
            db_session.add(row)
            db_session.flush()
            companies[key] = row
        return companies[key]

    def artifact(index: int) -> FinancialFactArtifact:
        while len(artifacts) <= index:
            name = f"2024q{len(artifacts) + 1}"
            row = FinancialFactArtifact(
                source_id=source.id,
                source_kind=FinancialFactSourceKind.SEC_FSDS,
                artifact_name=name,
                sha256=hashlib.sha256(name.encode()).hexdigest(),
                byte_length=10,
                raw_artifact_path=f"raw/{name}.zip",
                member_digests={},
                num_has_segments=True,
                ingested_at=NOW,
                created_at=NOW,
            )
            db_session.add(row)
            db_session.flush()
            artifacts.append(row)
        return artifacts[index]

    def make(
        accession: str,
        *,
        cik: str | None = CIK_A,
        company_key: str | None = None,
        form: SecFormType = SecFormType.FORM_10Q,
        filing_date: date = date(2024, 5, 3),
        report_date: date | None = None,
        periods: tuple[date, ...] = (date(2024, 3, 31),),
    ) -> SecFiling:
        """One stored filing with one submission per period in ``periods`` (none: no facts)."""
        filing = SecFiling(
            company_id=company(cik, company_key).id,
            accession_number=accession,
            form=form,
            filing_date=filing_date,
            report_date=report_date,
            accepted_at=datetime(2024, 5, 3, 20, 30, tzinfo=UTC),
            source_id=source.id,
            ingested_at=NOW,
            created_at=NOW,
        )
        db_session.add(filing)
        db_session.flush()
        for index, period_end in enumerate(periods):
            db_session.add(
                FinancialFactSubmission(
                    artifact_id=artifact(index).id,
                    sec_filing_id=filing.id,
                    period_end=period_end,
                    coregistrant_count=1,
                    created_at=NOW,
                )
            )
        db_session.flush()
        return filing

    return make


def _get(client: TestClient, **params: Any) -> dict[str, Any]:
    response = client.get("/api/v1/filings", params=params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    assert set(body) == {"items", "total", "limit", "offset"}
    return body


def _keys(node: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            found.add(key)
            found |= _keys(value)
    elif isinstance(node, list):
        for item in node:
            found |= _keys(item)
    return found


def _ids(body: dict[str, Any]) -> list[tuple[str, str | None]]:
    return [(item["accession"], item["company_cik"]) for item in body["items"]]


def acc(n: int, year: int = 24) -> str:
    return f"0000320193-{year:02d}-{n:06d}"


# ── empty, one row, fields ────────────────────────────────────────────────────────────────


def test_an_empty_index_is_http_200_with_total_zero(client: TestClient) -> None:
    assert _get(client) == {"items": [], "total": 0, "limit": 50, "offset": 0}


def test_a_stored_filing_without_fsds_submissions_is_not_listed(
    client: TestClient, seed: Seed
) -> None:
    seed(acc(1), periods=())
    seed(acc(2))

    body = _get(client)

    assert _ids(body) == [(acc(2), CIK_A)] and body["total"] == 1


def test_one_row_exposes_exactly_the_stored_metadata_and_nothing_else(
    client: TestClient, seed: Seed
) -> None:
    seed(
        acc(10),
        form=SecFormType.FORM_10K_A,
        filing_date=date(2024, 5, 17),
        report_date=date(2024, 3, 31),
        periods=(date(2024, 3, 31),),
    )

    body = _get(client)

    assert body["items"] == [
        {
            "accession": acc(10),
            "company_cik": CIK_A,
            "form": "10-K/A",
            "filing_date": "2024-05-17",
            "report_date": "2024-03-31",
            "fsds_period_end": "2024-03-31",
        }
    ]
    assert set(body["items"][0]) == ITEM_KEYS and not _keys(body) & FORBIDDEN_KEYS
    assert json.dumps(body).count("2099") == 0


def test_report_date_is_null_when_not_stored_and_is_never_taken_from_the_fsds_period(
    client: TestClient, seed: Seed
) -> None:
    seed(acc(1), report_date=None, periods=(date(2024, 3, 31),))

    item = _get(client)["items"][0]

    assert item["report_date"] is None and item["fsds_period_end"] == "2024-03-31"


def test_a_stored_filing_with_no_cik_company_is_listed_with_a_null_cik(
    client: TestClient, seed: Seed
) -> None:
    seed(acc(1), cik=None)

    item = _get(client)["items"][0]

    assert item["company_cik"] is None
    assert _get(client, cik=CIK_A)["total"] == 0


# ── join multiplication, count, fsds_period_end ───────────────────────────────────────────


def test_several_submissions_of_one_filing_never_inflate_rows_or_total(
    client: TestClient, seed: Seed
) -> None:
    seed(acc(1), periods=(date(2024, 3, 31),) * 3)
    seed(acc(2))

    body = _get(client)

    assert body["total"] == 2 and len(body["items"]) == 2
    assert _get(client, limit=1)["total"] == 2
    assert [i["accession"] for i in _get(client, limit=1, offset=1)["items"]] == [acc(1)]


def test_submissions_that_agree_on_the_period_expose_it(client: TestClient, seed: Seed) -> None:
    seed(acc(1), periods=(date(2024, 3, 31), date(2024, 3, 31)))

    assert _get(client)["items"][0]["fsds_period_end"] == "2024-03-31"


def test_submissions_that_disagree_give_null_not_an_arbitrary_period(
    client: TestClient, seed: Seed
) -> None:
    seed(acc(1), periods=(date(2024, 3, 31), date(2024, 6, 30)))
    seed(acc(2), periods=(date(2024, 6, 30), date(2024, 3, 31), date(2024, 3, 31)))

    body = _get(client)

    assert [i["fsds_period_end"] for i in body["items"]] == [None, None]
    assert body["total"] == 2


def test_the_period_column_cannot_be_null_so_no_null_period_case_exists(
    db_session: Session,
) -> None:
    assert FinancialFactSubmission.__table__.c.period_end.nullable is False


# ── ordering and pagination ───────────────────────────────────────────────────────────────


def test_the_order_is_filing_date_then_accession_descending_whatever_the_insert_order(
    client: TestClient, seed: Seed
) -> None:
    same_day = date(2024, 5, 3)
    for n in (3, 1, 2):
        seed(acc(n), filing_date=same_day)
    seed(acc(9), filing_date=date(2024, 5, 4))
    seed(acc(8), filing_date=date(2023, 1, 2))

    first = _ids(_get(client))
    assert first == [
        (acc(9), CIK_A),
        (acc(3), CIK_A),
        (acc(2), CIK_A),
        (acc(1), CIK_A),
        (acc(8), CIK_A),
    ]
    assert [_ids(_get(client)) for _ in range(3)] == [first] * 3


def test_a_joint_accession_stays_two_rows_ordered_by_cik_descending(
    client: TestClient, seed: Seed
) -> None:
    seed(acc(1), cik=CIK_A)
    seed(acc(1), cik=CIK_B)

    body = _get(client)

    assert _ids(body) == [(acc(1), CIK_B), (acc(1), CIK_A)] and body["total"] == 2
    assert _ids(_get(client, cik=CIK_A)) == [(acc(1), CIK_A)]
    assert _get(client, limit=1, offset=1)["items"][0]["company_cik"] == CIK_A


def test_a_cik_less_row_sorts_after_a_cik_row_of_the_same_accession_and_date(
    client: TestClient, seed: Seed
) -> None:
    seed(acc(1), cik=None, company_key="cikless")
    seed(acc(1), cik=CIK_B)
    seed(acc(1), cik=CIK_A)

    assert _ids(_get(client)) == [(acc(1), CIK_B), (acc(1), CIK_A), (acc(1), None)]


def test_joint_rows_without_any_cik_are_still_totally_ordered_and_never_duplicated(
    client: TestClient, seed: Seed
) -> None:
    # same accession and date under two companies that both lack a CIK: only the row id can break
    # the tie, and it must do so identically on every read and across page boundaries
    seed(acc(1), cik=None, company_key="first")
    seed(acc(1), cik=None, company_key="second")

    full = _get(client)
    paged = [_get(client, limit=1, offset=i)["items"][0] for i in range(2)]

    assert full["total"] == 2 and paged == full["items"]
    assert _get(client) == full


def test_pages_partition_the_rows_with_no_duplicate_or_missing_row(
    client: TestClient, seed: Seed
) -> None:
    for n in range(1, 8):
        seed(
            acc(n), filing_date=date(2024, 1, 1 + n % 3), periods=(date(2024, 3, 31),) * (n % 3 + 1)
        )
    seed(acc(3), cik=CIK_B, filing_date=date(2024, 1, 2))

    full = _ids(_get(client, limit=200))
    paged: list[tuple[str, str | None]] = []
    for offset in range(0, 8, 3):
        body = _get(client, limit=3, offset=offset)
        assert body["total"] == 8
        paged += _ids(body)

    assert paged == full and len(set(paged)) == len(paged) == 8


def test_an_offset_past_the_end_is_200_with_no_items_and_the_true_total(
    client: TestClient, seed: Seed
) -> None:
    seed(acc(1))
    seed(acc(2))

    body = _get(client, offset=500)

    assert body == {"items": [], "total": 2, "limit": 50, "offset": 500}


def test_the_documented_maximum_offset_and_limit_are_accepted(client: TestClient) -> None:
    assert _get(client, limit=200, offset=1_000_000)["items"] == []


# ── filters ───────────────────────────────────────────────────────────────────────────────


def test_the_form_filter_is_exact_and_never_merges_an_amendment_with_its_original(
    client: TestClient, seed: Seed
) -> None:
    for n, form in enumerate(
        (
            SecFormType.FORM_10Q,
            SecFormType.FORM_10Q_A,
            SecFormType.FORM_10K,
            SecFormType.FORM_10K_A,
        ),
        start=1,
    ):
        seed(acc(n), form=form)

    for n, wire_form in enumerate(("10-Q", "10-Q/A", "10-K", "10-K/A"), start=1):
        body = _get(client, form=wire_form)
        assert _ids(body) == [(acc(n), CIK_A)] and body["total"] == 1
        assert body["items"][0]["form"] == wire_form
    assert _get(client, form="8-K") == {"items": [], "total": 0, "limit": 50, "offset": 0}


def test_the_cik_filter_matches_exactly_and_a_valid_nonmatching_cik_is_an_empty_200(
    client: TestClient, seed: Seed
) -> None:
    seed(acc(1), cik=CIK_A)
    seed(acc(2), cik=CIK_B)

    assert _ids(_get(client, cik=CIK_B)) == [(acc(2), CIK_B)]
    assert _get(client, cik="0000000001") == {"items": [], "total": 0, "limit": 50, "offset": 0}
    assert client.get("/api/v1/filings", params={"cik": "555555"}).status_code == 422


def test_form_and_cik_compose_with_and_and_total_reflects_both(
    client: TestClient, seed: Seed
) -> None:
    seed(acc(1), cik=CIK_A, form=SecFormType.FORM_10Q)
    seed(acc(2), cik=CIK_A, form=SecFormType.FORM_10K)
    seed(acc(3), cik=CIK_B, form=SecFormType.FORM_10Q)
    seed(acc(4), cik=CIK_A, form=SecFormType.FORM_10Q, periods=(date(2024, 3, 31),) * 2)

    body = _get(client, form="10-Q", cik=CIK_A, limit=1)

    assert body["total"] == 2 and body["items"][0]["accession"] == acc(4)
    assert _get(client, form="10-Q")["total"] == 3 and _get(client, cik=CIK_A)["total"] == 3


# ── safety: forbidden fields, read-only, 503, repository ──────────────────────────────────


def test_the_response_never_carries_acceptance_availability_or_internal_fields(
    client: TestClient, db_session: Session, seed: Seed
) -> None:
    seed(acc(1))
    db_session.execute(sa.text("UPDATE sec_filing SET accepted_at = '2099-01-01T00:00:00Z'"))

    body = _get(client)

    assert not _keys(body) & FORBIDDEN_KEYS and "2099" not in json.dumps(body)


def test_a_get_issues_only_select_statements_and_changes_nothing(
    client: TestClient, db_session: Session, seed: Seed
) -> None:
    seed(acc(1), periods=(date(2024, 3, 31),) * 2)
    engine = db_session.get_bind()
    statements: list[str] = []

    def capture(_conn: Any, _cursor: Any, statement: str, *_a: Any) -> None:
        statements.append(statement.lstrip().split(None, 1)[0].upper())

    def counts() -> list[int]:
        return [
            db_session.query(SecFiling).count(),
            db_session.query(FinancialFactSubmission).count(),
            db_session.query(FinancialFactArtifact).count(),
        ]

    before = counts()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        _get(client)
        _get(client, form="10-Q", cik=CIK_A, limit=1, offset=5)
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert statements and set(statements) == {"SELECT"}, set(statements)
    assert counts() == before
    assert not db_session.new and not db_session.dirty and not db_session.deleted


def test_the_database_failure_is_a_generic_503_and_the_log_names_only_the_class(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    logged: list[str] = []

    class _RecordingLogger:
        def warning(self, message: str, *args: object, **kwargs: object) -> None:
            assert not kwargs.get("exc_info"), "no traceback: it carries the driver message"
            logged.append(message % args)

    monkeypatch.setattr(dependencies, "logger", _RecordingLogger())

    def boom(*_a: object, **_k: object) -> None:
        raise OperationalError("SELECT secret_table", {}, Exception("password=sup3rs3cret"))

    monkeypatch.setattr(FinancialFactRepository, "filing_index", boom)
    response = client.get("/api/v1/filings")

    assert response.status_code == 503
    assert response.json() == {"detail": "Database unavailable."}
    assert "sup3rs3cret" not in response.text and "secret_table" not in response.text
    assert logged == ["Filings index read failed: OperationalError"]
