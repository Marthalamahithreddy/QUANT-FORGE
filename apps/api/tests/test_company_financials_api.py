"""``GET /api/v1/companies`` and ``/companies/{cik}/financials`` without a database.

The handlers' reads are replaced by in-memory stubs; the real selectors, schemas and OpenAPI run.
The populated behaviour over real stored rows is exercised by the local smoke test (validation DB).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from quantforge.core.config import Settings
from quantforge.db.session import get_session
from quantforge.financials.company_financials import FilingFinancials, MetricComparison
from quantforge.financials.eps import EpsInputFact, EpsPeriodClass
from quantforge.financials.eps_change import EpsChangeStatus
from quantforge.financials.period_metrics import (
    MetricChange,
    MetricPoint,
    PointStatus,
    money_change,
)
from quantforge.main import create_app
from quantforge.models.enums import SecFormType
from quantforge.repositories.company import CompanyDirectoryPage, CompanyDirectoryRow
from quantforge.repositories.financial_fact import CompanyFilingFacts

CIK = "0000320187"
ACC = "0000320187-26-000037"


def _fact(value: str, end: date) -> EpsInputFact:
    return EpsInputFact(
        fact_id=uuid.uuid4(),
        accession_number=ACC,
        tag="NetIncomeLoss",
        taxonomy_version="us-gaap/2025",
        unit="USD",
        period_end=end,
        duration_quarters=1,
        value=Decimal(value),
        segments="",
        coreg="",
    )


def _filing() -> FilingFinancials:
    current = MetricPoint(
        PointStatus.AVAILABLE, "current", _fact("520000000.0000", date(2026, 2, 28))
    )
    prior = MetricPoint(PointStatus.AVAILABLE, "prior", _fact("794000000.0000", date(2025, 2, 28)))
    refused = MetricPoint(PointStatus.WRONG_UNIT, "only EUR exists")
    unavailable = MetricPoint(PointStatus.CURRENT_UNAVAILABLE, "no current")
    facts = CompanyFilingFacts(
        sec_filing_id=uuid.uuid4(),
        accession_number=ACC,
        form=SecFormType.FORM_10Q,
        filing_date=date(2026, 4, 1),
        report_date=date(2026, 2, 28),
        package_stored_at=datetime(2026, 10, 7, 16, 18, 22, tzinfo=UTC),
        submission_period_ends=(date(2026, 2, 28),),
        fiscal_year=2026,
        fiscal_period="Q3",
        artifacts=(("2026q2", "d" * 64),),
    )
    return FilingFinancials(
        facts,
        EpsPeriodClass.QUARTER,
        (
            MetricComparison("NET_INCOME", current, prior, money_change(current, prior)),
            MetricComparison(
                "REVENUE", refused, unavailable, MetricChange(EpsChangeStatus.INPUT_UNAVAILABLE)
            ),
        ),
    )


class _Repo:
    def __init__(self, _session: object) -> None:
        pass

    def get_by_cik(self, cik: str) -> object | None:
        return (
            SimpleNamespace(id=uuid.uuid4(), cik=cik, display_name="Nike, Inc.")
            if cik == CIK
            else None
        )

    def current_tickers(self, _company_id: object) -> tuple[str, ...]:
        return ("NKE",)

    def directory(self, *, query: str | None, limit: int, offset: int) -> CompanyDirectoryPage:
        rows = (CompanyDirectoryRow(CIK, "Nike, Inc.", ("NKE",), 164, 1),)
        assert (limit, offset) == (50, 0)
        return CompanyDirectoryPage(
            rows=rows if query in (None, "nike") else (), total=1 if query in (None, "nike") else 0
        )


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr("quantforge.api.v1.companies.CompanyRepository", _Repo)
    monkeypatch.setattr(
        "quantforge.api.v1.companies.build_company_financials", lambda _s, _c: [_filing()]
    )
    application = create_app(Settings(_env_file=None))
    application.dependency_overrides[get_session] = lambda: iter([None])
    with TestClient(application) as test_client:
        yield test_client


def test_directory_returns_stored_companies_and_echoes_paging(client: TestClient) -> None:
    body = client.get("/api/v1/companies", params={"q": "nike"}).json()
    assert body["total"] == 1 and body["limit"] == 50 and body["offset"] == 0
    assert body["items"][0] == {
        "cik": CIK,
        "name": "Nike, Inc.",
        "tickers": ["NKE"],
        "stored_filings": 164,
        "filings_with_financial_facts": 1,
    }
    assert client.get("/api/v1/companies", params={"q": "zzz"}).json()["items"] == []


@pytest.mark.parametrize(
    "query",
    ["limit=0", "limit=201", "offset=-1", "q=a&q=b", "x=1", f"q={'a' * 81}", "q=%00", "q=a%00b"],
)
def test_directory_rejects_bad_parameters(client: TestClient, query: str) -> None:
    assert client.get(f"/api/v1/companies?{query}").status_code == 422


def test_financials_map_exact_values_refusals_and_provenance(client: TestClient) -> None:
    body = client.get(f"/api/v1/companies/{CIK}/financials").json()
    assert body["company"] == {"cik": CIK, "name": "Nike, Inc.", "tickers": ["NKE"]}
    assert body["descriptive_only"] is True and body["no_point_in_time_claim"] is True
    filing = body["filings"][0]
    assert filing["accession"] == ACC and filing["form"] == "10-Q"
    assert filing["period_end"] == "2026-02-28" and filing["fiscal_period"] == "Q3"
    assert filing["package_stored_at"] == "2026-10-07T16:18:22Z"
    assert "accepted_at" not in filing
    assert filing["source_url"] == (
        "https://www.sec.gov/Archives/edgar/data/320187/000032018726000037/0000320187-26-000037-index.htm"
    )
    income, revenue = filing["metrics"]
    assert income["current"]["fact"]["value"] == "520000000.0000"  # a string, never a number
    assert income["change"]["absolute"] == "-274000000.0000"
    assert income["change"]["transition"] == "POSITIVE_DECREASED"
    assert revenue["current"] == {"status": "WRONG_UNIT", "reason": "only EUR exists", "fact": None}
    assert (
        revenue["change"]["status"] == "INPUT_UNAVAILABLE" and revenue["change"]["absolute"] is None
    )


def test_unknown_company_is_404_and_a_malformed_cik_is_422(client: TestClient) -> None:
    assert client.get("/api/v1/companies/0000000001/financials").status_code == 404
    assert client.get("/api/v1/companies/320187/financials").status_code == 422


def test_the_new_routes_are_get_only_and_in_the_openapi(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    for path in ("/api/v1/companies", "/api/v1/companies/{cik}/financials"):
        assert list(paths[path]) == ["get"]
    assert client.post("/api/v1/companies", json={}).status_code == 405
