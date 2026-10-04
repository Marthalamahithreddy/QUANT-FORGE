"""``GET /api/v1/filings/{accession}/eps?cik=...``: registrant-qualified filing identity.

The filing index identifies a filing by ``(company, accession)``; a joint accession is stored once per
registrant. These cases prove, over real stored rows and real HTTP, that an optional CIK selects the
registrant's own filing, never falls back to the accession alone, never borrows another registrant's
facts, and leaves the unqualified behaviour exactly as it was.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session
from tests.fsds_fixture import ACC_10K, ACC_10Q, CIK_MAIN
from tests.integration.test_api_filing_eps import (
    FORBIDDEN_KEYS,
    _keys,
)
from tests.integration.test_eps_extraction import (  # noqa: F401 - fixtures
    GAAP,
    NOW,
    _eps,
    _ingest,
    _store_other_company_filing,
    company,
    filings,
)

from quantforge.core.config import Settings
from quantforge.db.session import get_session
from quantforge.main import create_app
from quantforge.models.filing import SecFiling
from quantforge.models.financial_fact import (
    FinancialFact,
    FinancialFactArtifact,
    FinancialFactSubmission,
)

pytestmark = pytest.mark.integration

CIK_A = CIK_MAIN  # "0000320193": the `company` fixture's registrant
CIK_B = "0000555555"  # the co-registrant stored by `_store_other_company_filing`
CIK_UNRELATED = "0000999999"


def _get(client: TestClient, accession: str, cik: str | None = None, **overrides: str) -> Any:
    params = {"period_class": "QUARTER", "eps_kind": "DILUTED", **overrides}
    if cik is not None:
        params["cik"] = cik
    response = client.get(f"/api/v1/filings/{accession}/eps", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _add_facts(session: Session, filing: SecFiling, *, current: str, prior: str | None) -> None:
    """Directly store a submission and EPS facts for one specific stored filing."""
    artifact_id = session.scalars(sa.select(FinancialFactArtifact.id)).first()
    assert artifact_id is not None, "ingest company A first so an artifact exists"
    submission = FinancialFactSubmission(
        artifact_id=artifact_id,
        sec_filing_id=filing.id,
        period_end=date(2024, 3, 31),
        coregistrant_count=1,
        created_at=NOW,
    )
    session.add(submission)
    session.flush()
    rows = [(date(2024, 3, 31), current)] + ([(date(2023, 3, 31), prior)] if prior else [])
    for period_end, value in rows:
        session.add(
            FinancialFact(
                sec_filing_id=filing.id,
                submission_id=submission.id,
                tag="EarningsPerShareDiluted",
                version=GAAP,
                period_end=period_end,
                duration_quarters=1,
                unit="USD",
                segments="",
                coreg="",
                value=Decimal(value),
                created_at=NOW,
            )
        )
    session.commit()


@pytest.fixture
def client(db_session: Session) -> Iterator[TestClient]:
    application: FastAPI = create_app(Settings(_env_file=None))
    application.dependency_overrides[get_session] = lambda: db_session
    with TestClient(application) as test_client:
        yield test_client


@pytest.fixture
def joint(db_session: Session, tmp_path: Path, request: pytest.FixtureRequest) -> SecFiling:
    """ACC_10Q under registrant A (EPS 1.5300 vs 1.5200) and B (EPS 2.0000 vs 1.0000)."""
    request.getfixturevalue("filings")
    _ingest(
        db_session,
        tmp_path,
        [_eps(ACC_10Q, "20240331", "1", "1.5300"), _eps(ACC_10Q, "20230331", "1", "1.5200")],
        accessions=(ACC_10Q,),
    )
    other = _store_other_company_filing(db_session, ACC_10Q)
    _add_facts(db_session, other, current="2.0000", prior="1.0000")
    return other


def _single_company(db_session: Session, tmp_path: Path) -> None:
    _ingest(
        db_session,
        tmp_path,
        [_eps(ACC_10Q, "20240331", "1", "1.5300"), _eps(ACC_10Q, "20230331", "1", "1.5200")],
        accessions=(ACC_10Q,),
    )


# ── backward compatibility and the qualified happy path ──────────────────────────────────


@pytest.mark.usefixtures("filings")
def test_without_cik_the_response_is_exactly_the_unqualified_one(
    client: TestClient, db_session: Session, tmp_path: Path
) -> None:
    _single_company(db_session, tmp_path)

    unqualified = _get(client, ACC_10Q)
    qualified = _get(client, ACC_10Q, CIK_A)

    assert unqualified["current"]["status"] == "AVAILABLE"
    assert unqualified["company_cik"] == CIK_A and unqualified["accession"] == ACC_10Q
    assert qualified == unqualified, "a matching CIK changes nothing but which filing is meant"


@pytest.mark.usefixtures("filings")
def test_an_unqualified_single_registrant_unknown_accession_is_unchanged(
    client: TestClient,
) -> None:
    body = _get(client, "0000111111-24-000001")

    assert body["current"]["status"] == "NOT_INGESTED" and body["company_cik"] is None


@pytest.mark.parametrize(
    "bad", ["320193", "00003201930", "000032019X", " 0000320193", "0000320193 ", "", "cik"]
)
def test_a_malformed_cik_is_422(client: TestClient, bad: str) -> None:
    response = client.get(
        f"/api/v1/filings/{ACC_10Q}/eps",
        params={"period_class": "QUARTER", "eps_kind": "DILUTED", "cik": bad},
    )
    assert response.status_code == 422


def test_an_unknown_parameter_is_still_422(client: TestClient) -> None:
    response = client.get(
        f"/api/v1/filings/{ACC_10Q}/eps",
        params={"period_class": "QUARTER", "eps_kind": "DILUTED", "ticker": "AAPL"},
    )
    assert response.status_code == 422


# ── the joint accession ──────────────────────────────────────────────────────────────────


def test_a_joint_accession_without_a_cik_is_still_ambiguous(
    client: TestClient, joint: SecFiling
) -> None:
    body = _get(client, ACC_10Q)

    assert body["current"]["status"] == "AMBIGUOUS"
    assert body["comparative"]["status"] == "CURRENT_UNAVAILABLE"
    assert body["change"]["status"] == "INPUT_UNAVAILABLE"
    assert body["company_cik"] is None


def test_each_registrant_resolves_only_its_own_facts(client: TestClient, joint: SecFiling) -> None:
    a = _get(client, ACC_10Q, CIK_A)
    b = _get(client, ACC_10Q, CIK_B)

    assert (a["company_cik"], b["company_cik"]) == (CIK_A, CIK_B)
    assert (a["accession"], b["accession"]) == (ACC_10Q, ACC_10Q)
    assert a["current"]["fact"]["value"] == "1.5300"
    assert a["comparative"]["fact"]["value"] == "1.5200"
    assert a["change"]["eps_change"] == "0.0100"
    assert b["current"]["fact"]["value"] == "2.0000"
    assert b["comparative"]["fact"]["value"] == "1.0000"
    assert b["change"]["eps_change"] == "1.0000"
    assert b["change"]["percentage_change"] == "1"
    for body in (a, b):
        assert body["current"]["status"] == body["comparative"]["status"] == "AVAILABLE"
        assert body["change"]["status"] == "AVAILABLE"
    assert a["current"]["fact"]["fact_id"] != b["current"]["fact"]["fact_id"]


@pytest.mark.usefixtures("filings")
def test_a_registrant_never_borrows_the_other_registrants_facts(
    client: TestClient, db_session: Session, tmp_path: Path
) -> None:
    # facts exist ONLY under B; A's own filing of the accession has none ingested
    _ingest(
        db_session,
        tmp_path,
        [_eps(ACC_10K, "20230930", "4", "6.1300")],
        accessions=(ACC_10K,),
    )
    other = _store_other_company_filing(db_session, ACC_10Q)
    _add_facts(db_session, other, current="2.0000", prior="1.0000")

    a = _get(client, ACC_10Q, CIK_A)
    b = _get(client, ACC_10Q, CIK_B)

    assert a["current"]["status"] == "NOT_INGESTED" and a["current"]["fact"] is None
    assert a["comparative"]["status"] == "CURRENT_UNAVAILABLE"
    assert a["change"]["status"] == "INPUT_UNAVAILABLE"
    assert "2.0000" not in json.dumps(a) and "1.0000" not in json.dumps(a)
    assert b["current"]["fact"]["value"] == "2.0000"


@pytest.mark.usefixtures("filings")
def test_a_well_formed_unrelated_cik_is_a_qualified_not_ingested_never_a_fallback(
    client: TestClient, db_session: Session, tmp_path: Path
) -> None:
    _single_company(db_session, tmp_path)
    assert _get(client, ACC_10Q)["current"]["status"] == "AVAILABLE"  # the accession alone resolves

    body = _get(client, ACC_10Q, CIK_UNRELATED)

    assert body["current"]["status"] == "NOT_INGESTED"
    assert CIK_UNRELATED in body["current"]["reason"]
    assert body["company_cik"] == CIK_UNRELATED, "the requested identity is echoed"
    assert body["form"] is None and body["current"]["fact"] is None
    assert body["comparative"]["status"] == "CURRENT_UNAVAILABLE"
    assert body["comparative"]["fact"] is None
    assert body["change"]["status"] == "INPUT_UNAVAILABLE" and body["change"]["eps_change"] is None
    assert "1.5300" not in json.dumps(body)


# ── safety ───────────────────────────────────────────────────────────────────────────────


def test_qualified_responses_carry_no_availability_or_internal_fields(
    client: TestClient, joint: SecFiling
) -> None:
    for cik in (CIK_A, CIK_B, CIK_UNRELATED):
        body = _get(client, ACC_10Q, cik)
        assert not _keys(body) & FORBIDDEN_KEYS
        assert body["descriptive_only"] is True and body["no_point_in_time_claim"] is True


def test_qualified_and_unqualified_requests_only_select(
    client: TestClient, db_session: Session, joint: SecFiling
) -> None:
    engine = db_session.get_bind()
    statements: list[str] = []

    def capture(_conn: Any, _cursor: Any, statement: str, *_a: Any) -> None:
        statements.append(statement.lstrip().split(None, 1)[0].upper())

    tables = (SecFiling, FinancialFact, FinancialFactSubmission, FinancialFactArtifact)
    before = [db_session.query(table).count() for table in tables]
    event.listen(engine, "before_cursor_execute", capture)
    try:
        _get(client, ACC_10Q)
        _get(client, ACC_10Q, CIK_A)
        _get(client, ACC_10Q, CIK_B)
        _get(client, ACC_10Q, CIK_UNRELATED)
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert statements and set(statements) == {"SELECT"}, set(statements)
    assert [db_session.query(table).count() for table in tables] == before
    assert not db_session.new and not db_session.dirty and not db_session.deleted
