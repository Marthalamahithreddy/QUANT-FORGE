"""Query-count and page-size sanity for the filings index and the qualified EPS detail.

Not a benchmark: it proves the number of SQL statements does not grow with the number of rows
returned (no N+1), that a 50-row and a 200-row page are correct and deterministic, and records the
observed statement counts as small pinned upper bounds.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session
from tests.vertical_scenario import ACC_HAPPY, CIK_A, seed_scenario

from quantforge.core.config import Settings
from quantforge.db.session import get_session
from quantforge.ingestion.sources import get_or_create_data_source
from quantforge.main import create_app
from quantforge.models.company import Company
from quantforge.models.enums import FinancialFactSourceKind, ProviderType, SecFormType
from quantforge.models.filing import SecFiling
from quantforge.models.financial_fact import (
    FinancialFact,
    FinancialFactArtifact,
    FinancialFactSubmission,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
_HARNESS = ("SAVEPOINT", "RELEASE SAVEPOINT", "ROLLBACK TO SAVEPOINT")
ROWS = 230
#: Observed, and pinned exactly: index = 2 statements (count + page) for 50 and for 200 rows;
#: qualified EPS detail = 3 (filing contexts, submission periods, facts); an ambiguous unqualified
#: joint accession = 2 (it stops before reading any fact).
INDEX_STATEMENTS = 2
QUALIFIED_EPS_STATEMENTS = 3
AMBIGUOUS_EPS_STATEMENTS = 2


@pytest.fixture
def client(db_session: Session) -> Iterator[TestClient]:
    application: FastAPI = create_app(Settings(_env_file=None))
    application.dependency_overrides[get_session] = lambda: db_session
    with TestClient(application) as test_client:
        yield test_client


def _seed_many(session: Session) -> None:
    """ROWS filings across 23 registrants (CIK-carrying), several sharing a filing date."""
    source = get_or_create_data_source(
        session, name="sec-edgar", provider_type=ProviderType.REGULATORY_FILING
    )
    artifact = FinancialFactArtifact(
        source_id=source.id,
        source_kind=FinancialFactSourceKind.SEC_FSDS,
        artifact_name="scale",
        sha256=hashlib.sha256(b"scale").hexdigest(),
        byte_length=1,
        raw_artifact_path="raw/scale.zip",
        member_digests={},
        num_has_segments=True,
        ingested_at=NOW,
        created_at=NOW,
    )
    session.add(artifact)
    companies = [
        Company(legal_name=f"Scale {i}", display_name=f"Scale {i}", cik=f"{1_000_000 + i:010d}")
        for i in range(23)
    ]
    session.add_all(companies)
    session.flush()
    filings = []
    for n in range(ROWS):
        filing = SecFiling(
            company_id=companies[n % 23].id,
            accession_number=f"0001000000-24-{n:06d}",
            form=SecFormType.FORM_10Q,
            filing_date=date(2024, 1, 1) + timedelta(days=n // 4),  # four filings share a date
            accepted_at=NOW,
            source_id=source.id,
            ingested_at=NOW,
            created_at=NOW,
        )
        filings.append(filing)
    session.add_all(filings)
    session.flush()
    session.add_all(
        FinancialFactSubmission(
            artifact_id=artifact.id,
            sec_filing_id=f.id,
            period_end=date(2024, 3, 31),
            coregistrant_count=1,
            created_at=NOW,
        )
        for f in filings
    )
    session.commit()


def _count(session: Session, call: Any) -> tuple[Any, int]:
    engine = session.get_bind()
    statements: list[str] = []

    def capture(_c: Any, _cur: Any, statement: str, *_a: Any) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        result = call()
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    # the test session joins a SAVEPOINT; those statements belong to the harness, not the request
    statements = [s for s in statements if not s.lstrip().upper().startswith(_HARNESS)]
    assert all(s.lstrip().upper().startswith("SELECT") for s in statements), statements[:3]
    return result, len(statements)


def test_default_and_maximum_pages_are_correct_deterministic_and_never_n_plus_one(
    client: TestClient, db_session: Session
) -> None:
    _seed_many(db_session)

    small, small_count = _count(db_session, lambda: client.get("/api/v1/filings").json())
    large, large_count = _count(
        db_session, lambda: client.get("/api/v1/filings", params={"limit": 200}).json()
    )

    assert (small["total"], large["total"]) == (ROWS, ROWS)
    assert (len(small["items"]), len(large["items"])) == (50, 200)
    assert large["items"][:50] == small["items"], "the 50-row page is the head of the 200-row page"
    assert small_count == large_count == INDEX_STATEMENTS, (small_count, large_count)

    pairs = [(i["accession"], i["company_cik"]) for i in large["items"]]
    assert len(set(pairs)) == 200, "no duplicated logical row"
    keys = [(i["filing_date"], i["accession"]) for i in large["items"]]
    assert keys == sorted(keys, reverse=True), "filing_date DESC, accession DESC"
    again = client.get("/api/v1/filings", params={"limit": 200}).json()
    assert again == large, "unchanged data, identical page"


def test_pages_partition_a_large_index_with_no_gap_or_repeat(
    client: TestClient, db_session: Session
) -> None:
    _seed_many(db_session)
    seen: list[tuple[str, str | None]] = []
    for offset in range(0, ROWS + 50, 200):
        body = client.get("/api/v1/filings", params={"limit": 200, "offset": offset}).json()
        assert body["total"] == ROWS
        seen += [(i["accession"], i["company_cik"]) for i in body["items"]]
    assert len(seen) == ROWS and len(set(seen)) == ROWS


def _eps_get(client: TestClient, accession: str, cik: str | None) -> Any:
    params = {"period_class": "QUARTER", "eps_kind": "DILUTED"}
    if cik is not None:
        params["cik"] = cik
    return client.get(f"/api/v1/filings/{accession}/eps", params=params).json()


def test_the_eps_detail_statement_counts_are_pinned_per_path(
    client: TestClient, db_session: Session
) -> None:
    seed_scenario(db_session)

    qualified_body, qualified = _count(db_session, lambda: _eps_get(client, ACC_HAPPY, CIK_A))
    ambiguous_body, ambiguous = _count(db_session, lambda: _eps_get(client, ACC_HAPPY, None))

    assert qualified_body["current"]["status"] == "AVAILABLE"
    assert ambiguous_body["current"]["status"] == "AMBIGUOUS"
    assert qualified == QUALIFIED_EPS_STATEMENTS
    assert ambiguous == AMBIGUOUS_EPS_STATEMENTS


def test_the_eps_detail_statement_count_does_not_grow_with_the_number_of_stored_facts(
    client: TestClient, db_session: Session
) -> None:
    seed_scenario(db_session)
    _, before = _count(db_session, lambda: _eps_get(client, ACC_HAPPY, CIK_A))
    filing = (
        db_session.query(SecFiling)
        .filter(SecFiling.accession_number == ACC_HAPPY)
        .join(Company, Company.id == SecFiling.company_id)
        .filter(Company.cik == CIK_A)
        .one()
    )
    submission = (
        db_session.query(FinancialFactSubmission)
        .filter(FinancialFactSubmission.sec_filing_id == filing.id)
        .one()
    )
    db_session.add_all(
        FinancialFact(
            sec_filing_id=filing.id,
            submission_id=submission.id,
            tag="EarningsPerShareDiluted",
            version="us-gaap/2024",
            period_end=date(2000 + y, m, 28),
            duration_quarters=q,
            unit="USD",
            segments="",
            coreg="",
            value=Decimal("1.0000"),
            created_at=NOW,
        )
        for y in range(1, 25)
        for m in (3, 6, 9, 12)
        for q in (2, 3)
    )
    db_session.commit()

    body, after = _count(db_session, lambda: _eps_get(client, ACC_HAPPY, CIK_A))

    assert body["current"]["fact"]["value"] == "1.5300", "the extra facts change nothing selected"
    assert after == before == QUALIFIED_EPS_STATEMENTS
