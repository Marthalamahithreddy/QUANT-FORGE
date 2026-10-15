"""``GET /api/v1/companies`` and ``/companies/{cik}/financials`` over real stored rows (PostgreSQL).

The unit tests stub the repository; this file runs the real SQL (directory search, counts, filing
query, ordering) and the real selectors over FSDS-shaped facts ingested through the real service.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from tests.fsds_fixture import ACC_10K, ACC_10Q, ACC_10QA, CIK_MAIN, build_package, num_row
from tests.integration.test_eps_extraction import (  # noqa: F401 - fixtures
    GAAP,
    NOW,
    _eps,
    _ingest,
    company,
    filings,
)

from quantforge.core.config import Settings
from quantforge.db.session import get_session
from quantforge.financials.company_financials import build_company_financials
from quantforge.ingestion.financial_facts import FinancialFactIngestionService
from quantforge.ingestion.raw_store import RawResponseStore
from quantforge.main import create_app
from quantforge.models.company import Company

pytestmark = [
    pytest.mark.integration,
    pytest.mark.usefixtures("filings"),
]  # the company and its three filings

REVENUE = "RevenueFromContractWithCustomerExcludingAssessedTax"
NET_INCOME = "NetIncomeLoss"


def _money(adsh: str, tag: str, ddate: str, qtrs: str, value: str) -> dict[str, str]:
    return num_row(adsh=adsh, tag=tag, version=GAAP, ddate=ddate, qtrs=qtrs, value=value)


def _nums() -> list[dict[str, str]]:
    return [
        # 10-Q: standalone quarter, its prior-year comparative, and a year-to-date amount that must
        # never be taken for the quarter.
        _money(ACC_10Q, REVENUE, "20240331", "1", "1100.0000"),
        _money(ACC_10Q, REVENUE, "20230331", "1", "1000.0000"),
        _money(ACC_10Q, REVENUE, "20240331", "2", "2300.0000"),
        _money(ACC_10Q, NET_INCOME, "20240331", "1", "-50.0000"),
        _money(ACC_10Q, NET_INCOME, "20230331", "1", "-100.0000"),
        _eps(ACC_10Q, "20240331", "1", "1.5300"),
        _eps(ACC_10Q, "20230331", "1", "1.5200"),
        # 10-K: annual.
        _money(ACC_10K, REVENUE, "20230930", "4", "4000.0000"),
        _money(ACC_10K, REVENUE, "20220930", "4", "3000.0000"),
        # 10-Q/A: only EPS, so revenue and net income must be named refusals.
        _eps(ACC_10QA, "20240331", "1", "1.5400"),
    ]


@pytest.fixture
def client(db_session: Session) -> Iterator[TestClient]:
    application: FastAPI = create_app(Settings(_env_file=None))
    application.dependency_overrides[get_session] = lambda: db_session
    with TestClient(application) as test_client:
        yield test_client


def _point(metric: dict[str, Any], side: str) -> tuple[str, str | None]:
    section = metric[side]
    return section["status"], section["fact"]["value"] if section["fact"] else None


def test_directory_searches_by_name_and_cik_and_counts_stored_filings(
    client: TestClient, db_session: Session, tmp_path: Path
) -> None:
    _ingest(db_session, tmp_path, _nums())
    db_session.add(Company(legal_name="No Cik Co", display_name="No Cik Co", cik=None))
    db_session.commit()

    everything = client.get("/api/v1/companies").json()
    assert everything["total"] == 1  # the company without a CIK cannot be opened, so is not listed
    item = everything["items"][0]
    assert item["cik"] == CIK_MAIN and item["name"] == "Example Filer"
    assert (item["stored_filings"], item["filings_with_financial_facts"]) == (3, 3)

    for query in ("example", "EXAMPLE FIL", CIK_MAIN, "320193"):
        assert client.get("/api/v1/companies", params={"q": query}).json()["total"] == 1, query
    for query in ("zzz", "%", "_", "\\", "Example%Filer"):
        assert client.get("/api/v1/companies", params={"q": query}).json()["total"] == 0, query


def test_directory_pages_and_an_offset_past_the_end_is_an_empty_page(client: TestClient) -> None:
    body = client.get("/api/v1/companies", params={"limit": 1, "offset": 5}).json()
    assert body["items"] == [] and body["total"] == 1 and body["offset"] == 5


def test_financials_select_the_quarter_not_the_ytd_and_pair_it_with_the_prior_year(
    client: TestClient, db_session: Session, tmp_path: Path
) -> None:
    _ingest(db_session, tmp_path, _nums())
    body = client.get(f"/api/v1/companies/{CIK_MAIN}/financials").json()

    assert [f["accession"] for f in body["filings"]] == [
        ACC_10QA,
        ACC_10Q,
        ACC_10K,
    ]  # newest period first
    assert body["total_filings"] == 3
    quarter = next(f for f in body["filings"] if f["accession"] == ACC_10Q)
    assert quarter["period_class"] == "QUARTER" and quarter["period_end"] == "2024-03-31"
    revenue, income, eps = quarter["metrics"]
    assert [m["metric"] for m in quarter["metrics"]] == ["REVENUE", "NET_INCOME", "EPS_DILUTED"]
    assert _point(revenue, "current") == ("AVAILABLE", "1100.0000")  # not the 2300 year-to-date
    assert _point(revenue, "prior") == ("AVAILABLE", "1000.0000")
    assert revenue["change"]["absolute"] == "100.0000"
    assert revenue["change"]["ratio"].startswith("0.1")
    assert _point(income, "current") == ("AVAILABLE", "-50.0000")
    assert income["change"]["absolute"] == "50.0000"
    assert income["change"]["ratio"] is None
    assert income["change"]["ratio_unavailable_reason"] == "NEGATIVE_COMPARATIVE"
    assert _point(eps, "current") == ("AVAILABLE", "1.5300")
    assert eps["change"]["absolute"] == "0.0100"


def test_annual_filing_is_annual_and_missing_metrics_are_named_refusals(
    client: TestClient, db_session: Session, tmp_path: Path
) -> None:
    _ingest(db_session, tmp_path, _nums())
    body = client.get(f"/api/v1/companies/{CIK_MAIN}/financials").json()
    annual = next(f for f in body["filings"] if f["accession"] == ACC_10K)
    assert annual["period_class"] == "ANNUAL" and annual["form"] == "10-K"
    assert _point(annual["metrics"][0], "current") == ("AVAILABLE", "4000.0000")
    assert annual["metrics"][0]["change"]["absolute"] == "1000.0000"
    assert (
        annual["metrics"][1]["current"]["status"] == "MISSING"
    )  # no net income stored: named, never 0
    assert annual["metrics"][1]["change"]["status"] == "INPUT_UNAVAILABLE"

    amendment = next(f for f in body["filings"] if f["accession"] == ACC_10QA)
    assert amendment["form"] == "10-Q/A"  # its own row; nothing is merged with the original
    assert amendment["metrics"][0]["current"]["status"] == "MISSING"
    assert _point(amendment["metrics"][2], "current") == ("AVAILABLE", "1.5400")


def test_provenance_is_present_and_no_acceptance_time_is_exposed(
    client: TestClient, db_session: Session, tmp_path: Path
) -> None:
    _ingest(db_session, tmp_path, _nums())
    text = client.get(f"/api/v1/companies/{CIK_MAIN}/financials").text
    body = client.get(f"/api/v1/companies/{CIK_MAIN}/financials").json()
    first = body["filings"][0]
    assert first["source_artifact"] == "2024q2" and len(first["source_artifact_sha256"]) == 64
    assert first["source_url"].startswith("https://www.sec.gov/Archives/edgar/data/320193/")
    assert first["package_stored_at"].endswith("Z")
    for forbidden in ("accepted_at", "observed_at", "available_at", "raw_artifact_path"):
        assert forbidden not in text


def test_a_company_without_facts_is_an_honest_empty_list_and_an_unknown_cik_is_404(
    client: TestClient,
) -> None:
    assert client.get(f"/api/v1/companies/{CIK_MAIN}/financials").json()["filings"] == []
    assert client.get("/api/v1/companies/0000000001/financials").status_code == 404


def test_reading_writes_nothing(client: TestClient, db_session: Session, tmp_path: Path) -> None:
    _ingest(db_session, tmp_path, _nums())
    client.get(f"/api/v1/companies/{CIK_MAIN}/financials")
    client.get("/api/v1/companies", params={"q": "example"})
    assert not db_session.new and not db_session.dirty and not db_session.deleted


def test_a_filing_in_two_packages_reports_one_package_with_its_own_stored_time(
    client: TestClient, db_session: Session, tmp_path: Path
) -> None:
    """The artifact name/hash and ``package_stored_at`` must describe the SAME (earliest) package.

    The earliest-stored package is named ``2024q3``, which sorts AFTER the later ``2024q2``: pairing
    the alphabetically first name with the earliest time would mix two packages.
    """
    # The packages differ only by a fact of another period (a conflicting value would be refused).
    for name, when, extra_period in (
        ("2024q3", NOW, "20220331"),
        ("2024q2", NOW + timedelta(days=1), "20210331"),
    ):
        package = build_package(
            tmp_path / f"{name}.zip",
            nums=[*_nums(), _money(ACC_10Q, REVENUE, extra_period, "1", "900.0000")],
        )
        FinancialFactIngestionService(
            session=db_session, store=RawResponseStore(root=tmp_path / f"raw-{name}"), now=when
        ).ingest_fsds_package(package, package_name=name, accessions=(ACC_10Q,))
        db_session.commit()

    body = client.get(f"/api/v1/companies/{CIK_MAIN}/financials").json()
    quarter = next(f for f in body["filings"] if f["accession"] == ACC_10Q)
    assert quarter["source_artifact"] == "2024q3"
    assert quarter["package_stored_at"] == "2026-10-03T12:00:00Z"


def test_the_list_is_capped_and_the_total_says_so(db_session: Session, tmp_path: Path) -> None:
    """Only the newest ``limit`` filings are read, and the company's full count is still reported."""
    _ingest(db_session, tmp_path, _nums())
    stored = db_session.query(Company).filter(Company.cik == CIK_MAIN).one()
    history = build_company_financials(db_session, stored, limit=2)
    assert [f.filing.accession_number for f in history.filings] == [ACC_10QA, ACC_10Q]
    assert history.total_filings == 3
