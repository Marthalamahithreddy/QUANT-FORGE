"""``GET /api/v1/filings/{accession}/eps`` over real stored FSDS facts, via real HTTP (TestClient).

Every case ingests a tiny FSDS-shaped package, then asserts on the actual JSON response: string
Decimals, statuses passed through unmodified, domain refusals as HTTP 200, no availability fields,
and that a GET only reads.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from decimal import ROUND_HALF_EVEN, Context, Decimal
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from tests.fsds_fixture import ACC_10K, ACC_10Q, ACC_10QA, num_row
from tests.integration.test_eps_extraction import (  # noqa: F401 - fixtures
    CLASS_A,
    GAAP,
    _eps,
    _ingest,
    company,
    filings,
)

from quantforge.api.v1 import dependencies, filing_eps
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

FORBIDDEN_KEYS = {
    "accepted_at",
    "source_accepted",
    "ingested_at",
    "observed_at",
    "available_at",
    "known_as_of",
    "tradable",
    "created_at",
    "ingestion_run_id",
    "raw_artifact_path",
}
RATIO_CONTEXT = Context(prec=50, rounding=ROUND_HALF_EVEN, traps=[], flags=[])


@pytest.fixture
def client(db_session: Session) -> Iterator[TestClient]:
    application: FastAPI = create_app(Settings(_env_file=None))
    application.dependency_overrides[get_session] = lambda: db_session
    with TestClient(application) as test_client:
        yield test_client


def _get(
    client: TestClient,
    accession: str,
    period_class: str = "QUARTER",
    eps_kind: str = "DILUTED",
) -> dict[str, Any]:
    response = client.get(
        f"/api/v1/filings/{accession}/eps",
        params={"period_class": period_class, "eps_kind": eps_kind},
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
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


def _value_strings(body: dict[str, Any]) -> list[Any]:
    return [
        body["current"]["fact"] and body["current"]["fact"]["value"],
        body["comparative"]["fact"] and body["comparative"]["fact"]["value"],
        body["change"]["eps_change"],
        body["change"]["percentage_change"],
    ]


def _assert_boundary(body: dict[str, Any]) -> None:
    assert body["descriptive_only"] is True and body["no_point_in_time_claim"] is True
    assert "No point-in-time availability claim" in body["boundary_note"]
    assert not _keys(body) & FORBIDDEN_KEYS
    for value in _value_strings(body):
        assert value is None or (isinstance(value, str) and not re.search("[eE]", value))


def _setup(
    db_session: Session, tmp_path: Path, current: str, prior: str | None, **prior_extra: str
) -> None:
    rows = [_eps(ACC_10Q, "20240331", "1", current)]
    if prior is not None:
        rows.append(_eps(ACC_10Q, "20230331", "1", prior, **prior_extra))
    _ingest(db_session, tmp_path, rows, accessions=(ACC_10Q,))


# ── the AVAILABLE chain ──────────────────────────────────────────────────────────────────


@pytest.mark.usefixtures("filings")
def test_an_available_chain_returns_exact_string_decimals_evidence_and_boundary_flags(
    client: TestClient, db_session: Session, tmp_path: Path
) -> None:
    _setup(db_session, tmp_path, "1.5300", "1.5200")

    body = _get(client, ACC_10Q)

    assert (body["accession"], body["form"], body["company_cik"]) == (ACC_10Q, "10-Q", "0000320193")
    assert (body["period_class"], body["eps_kind"]) == ("QUARTER", "DILUTED")
    assert body["current"]["status"] == "AVAILABLE" and body["comparative"]["status"] == "AVAILABLE"
    assert body["change"]["status"] == "AVAILABLE"
    fact = body["current"]["fact"]
    assert fact["value"] == "1.5300" and fact["unit"] == "USD" and fact["qtrs"] == 1
    assert (fact["tag"], fact["taxonomy_version"]) == ("EarningsPerShareDiluted", GAAP)
    assert (fact["period_end"], fact["segments"], fact["coreg"]) == ("2024-03-31", "", "")
    comparative = body["comparative"]["fact"]
    assert comparative["value"] == "1.5200" and comparative["period_end"] == "2023-03-31"
    assert body["comparative"]["meaning"] == "comparative EPS as presented in this filing"
    assert body["change"]["eps_change"] == "0.0100"
    assert body["change"]["transition"] == "POSITIVE_INCREASED"
    expected = format(RATIO_CONTEXT.divide(Decimal("0.0100"), Decimal("1.5200")), "f")
    assert body["change"]["percentage_change"] == expected
    assert body["change"]["percentage_semantics"] == "ratio"
    assert body["change"]["percentage_unavailable_reason"] is None
    _assert_boundary(body)
    assert isinstance(fact["qtrs"], int) and not isinstance(fact["value"], int | float)


@pytest.mark.parametrize(
    ("current", "prior", "transition", "pct", "reason"),
    [
        ("1.50", "1.00", "POSITIVE_INCREASED", "0.5", None),
        ("0.50", "1.00", "POSITIVE_DECREASED", "-0.5", None),
        (
            "-0.5000",
            "1.0000",
            "PROFIT_TO_LOSS",
            "-1.5",
            None,
        ),  # the intentional positive-base policy
        ("0", "1", "PROFIT_TO_ZERO", "-1", None),
        ("1.5", "0", "ZERO_TO_PROFIT", None, "ZERO_COMPARATIVE"),
        ("0", "0", "ZERO_UNCHANGED", None, "ZERO_COMPARATIVE"),
        ("-1.5", "0", "ZERO_TO_LOSS", None, "ZERO_COMPARATIVE"),
        ("-0.5", "-1.0", "LOSS_NARROWED", None, "NEGATIVE_COMPARATIVE"),
        ("-1.5", "-1.0", "LOSS_WIDENED", None, "NEGATIVE_COMPARATIVE"),
        ("0.5", "-1.0", "LOSS_TO_PROFIT", None, "NEGATIVE_COMPARATIVE"),
        ("0", "-1.0", "LOSS_TO_ZERO", None, "NEGATIVE_COMPARATIVE"),
    ],
)
@pytest.mark.usefixtures("filings")
def test_the_change_section_serialises_transition_and_percentage_availability(
    client: TestClient,
    db_session: Session,
    tmp_path: Path,
    current: str,
    prior: str,
    transition: str,
    pct: str | None,
    reason: str | None,
) -> None:
    _setup(db_session, tmp_path, current, prior)

    body = _get(client, ACC_10Q)

    change = body["change"]
    assert change["status"] == "AVAILABLE", (
        "a missing percentage never makes the change unavailable"
    )
    assert change["transition"] == transition
    assert change["eps_change"] == format(Decimal(current) - Decimal(prior), "f") or Decimal(
        change["eps_change"]
    ) == Decimal(current) - Decimal(prior)
    assert isinstance(change["eps_change"], str)
    assert change["percentage_unavailable_reason"] == reason
    if pct is None:
        assert change["percentage_change"] is None
    else:
        assert Decimal(change["percentage_change"]) == Decimal(pct)
        assert isinstance(change["percentage_change"], str)
    _assert_boundary(body)


# ── comparative refusals pass through untouched ──────────────────────────────────────────

COMPARATIVE_CASES = {
    "COMPARATIVE_MISSING": (None, {}),
    "COMPARATIVE_PERIOD_MISMATCH": ("20230228", {}),
    "COMPARATIVE_DIMENSIONAL_ONLY": ("20230331", {"segments": CLASS_A}),
    "COMPARATIVE_COREG_ONLY": ("20230331", {"coreg": "ExampleOperatingPartnershipLP"}),
    "COMPARATIVE_WRONG_UNIT": ("20230331", {"uom": "CAD"}),
    "COMPARATIVE_NULL_VALUE": ("20230331", {"value": ""}),
}


@pytest.mark.parametrize("expected", sorted(COMPARATIVE_CASES))
@pytest.mark.usefixtures("filings")
def test_every_comparative_refusal_is_http_200_with_its_own_status_and_an_unavailable_change(
    client: TestClient,
    db_session: Session,
    tmp_path: Path,
    expected: str,
) -> None:
    ddate, extra = COMPARATIVE_CASES[expected]
    rows = [_eps(ACC_10Q, "20240331", "1", "1.5300")]
    if ddate is not None:
        value = extra.get("value", "1.5200")
        rest = {k: v for k, v in extra.items() if k != "value"}
        rows.append(_eps(ACC_10Q, ddate, "1", value, **rest))
    _ingest(db_session, tmp_path, rows, accessions=(ACC_10Q,))

    body = _get(client, ACC_10Q)

    assert body["current"]["status"] == "AVAILABLE"
    assert body["comparative"]["status"] == expected
    assert body["comparative"]["reason"] and body["comparative"]["reason"] != "not available"
    assert (body["comparative"]["fact"] is not None) == (expected == "COMPARATIVE_NULL_VALUE")
    assert body["change"] == {
        "status": "INPUT_UNAVAILABLE",
        "eps_change": None,
        "transition": None,
        "percentage_change": None,
        "percentage_semantics": "ratio",
        "percentage_unavailable_reason": None,
    }
    _assert_boundary(body)


@pytest.mark.usefixtures("filings")
def test_an_ambiguous_comparative_is_passed_through(
    client: TestClient, db_session: Session, tmp_path: Path
) -> None:
    _ingest(
        db_session,
        tmp_path,
        [
            _eps(ACC_10Q, "20240331", "1", "1.5300"),
            _eps(ACC_10Q, "20230331", "1", "1.5200", version="us-gaap/2023"),
            _eps(ACC_10Q, "20230331", "1", "1.5200", version="us-gaap/2022"),
        ],
        accessions=(ACC_10Q,),
    )
    body = _get(client, ACC_10Q)
    assert body["comparative"]["status"] == "COMPARATIVE_AMBIGUOUS"
    assert body["change"]["status"] == "INPUT_UNAVAILABLE"


# ── current refusals propagate truthfully ────────────────────────────────────────────────

CURRENT_CASES = {
    "MISSING": (
        [num_row(adsh=ACC_10Q, tag="NetIncomeLoss", version=GAAP, ddate="20240331", qtrs="1")]
    ),
    "WRONG_UNIT": ([_eps(ACC_10Q, "20240331", "1", "1.5300", uom="CAD")]),
    "DIMENSIONAL_ONLY": ([_eps(ACC_10Q, "20240331", "1", "1.5300", segments=CLASS_A)]),
    "COREG_ONLY": (
        [_eps(ACC_10Q, "20240331", "1", "1.5300", coreg="ExampleOperatingPartnershipLP")]
    ),
    "NULL_VALUE": ([_eps(ACC_10Q, "20240331", "1", "")]),
    "AMBIGUOUS": (
        [
            _eps(ACC_10Q, "20240331", "1", "1.5300", version="us-gaap/2024"),
            _eps(ACC_10Q, "20240331", "1", "1.5300", version="us-gaap/2023"),
        ]
    ),
}


@pytest.mark.parametrize("expected", sorted(CURRENT_CASES))
@pytest.mark.usefixtures("filings")
def test_every_current_refusal_propagates_through_comparative_and_change(
    client: TestClient,
    db_session: Session,
    tmp_path: Path,
    expected: str,
) -> None:
    rows = [*CURRENT_CASES[expected], _eps(ACC_10Q, "20230331", "1", "1.5200")]
    _ingest(db_session, tmp_path, rows, accessions=(ACC_10Q,))

    body = _get(client, ACC_10Q)

    assert body["current"]["status"] == expected and body["current"]["reason"]
    assert body["comparative"]["status"] == "CURRENT_UNAVAILABLE"
    assert expected in body["comparative"]["reason"], "the underlying current status is named"
    assert body["comparative"]["fact"] is None
    assert body["change"]["status"] == "INPUT_UNAVAILABLE"
    assert (body["current"]["fact"] is not None) == (expected == "NULL_VALUE")
    _assert_boundary(body)


@pytest.mark.usefixtures("filings")
def test_a_well_formed_accession_that_is_not_ingested_is_http_200_not_404(
    client: TestClient,
) -> None:
    body = _get(client, "0000111111-24-000001")
    assert body["current"]["status"] == "NOT_INGESTED"
    assert "not evidence" in body["current"]["reason"]
    assert body["comparative"]["status"] == "CURRENT_UNAVAILABLE"
    assert body["change"]["status"] == "INPUT_UNAVAILABLE"
    assert body["form"] is None and body["company_cik"] is None
    assert body["current"]["fact"] is None and body["comparative"]["fact"] is None
    _assert_boundary(body)


@pytest.mark.usefixtures("filings")
def test_a_stored_filing_without_ingested_facts_is_not_ingested_with_its_form(
    client: TestClient, db_session: Session, tmp_path: Path
) -> None:
    _setup(db_session, tmp_path, "1.5300", "1.5200")  # only ACC_10Q is ingested
    body = _get(client, ACC_10K, "ANNUAL")
    assert body["current"]["status"] == "NOT_INGESTED" and body["form"] == "10-K"


# ── kinds, period classes, no inference ──────────────────────────────────────────────────


@pytest.mark.usefixtures("filings")
def test_basic_and_diluted_reaches_its_own_concept_with_no_fallback(
    client: TestClient, db_session: Session, tmp_path: Path
) -> None:
    both = "EarningsPerShareBasicAndDiluted"
    _ingest(
        db_session,
        tmp_path,
        [
            _eps(ACC_10Q, "20240331", "1", "2.0000", tag=both),
            _eps(ACC_10Q, "20230331", "1", "1.9000", tag=both),
        ],
        accessions=(ACC_10Q,),
    )
    combined = _get(client, ACC_10Q, eps_kind="BASIC_AND_DILUTED")
    diluted = _get(client, ACC_10Q, eps_kind="DILUTED")
    assert combined["eps_kind"] == "BASIC_AND_DILUTED"
    assert combined["current"]["fact"]["tag"] == both
    assert combined["comparative"]["fact"]["value"] == "1.9000"
    assert diluted["current"]["status"] == "MISSING", "no fallback to the other kind"
    assert diluted["change"]["status"] == "INPUT_UNAVAILABLE"


@pytest.mark.usefixtures("filings")
def test_annual_and_quarter_are_explicit_and_never_inferred_from_the_form(
    client: TestClient, db_session: Session, tmp_path: Path
) -> None:
    _ingest(
        db_session,
        tmp_path,
        [
            _eps(ACC_10K, "20230930", "4", "6.1300", version="us-gaap/2023"),
            _eps(ACC_10K, "20220930", "4", "5.6700", version="us-gaap/2023"),
            _eps(ACC_10K, "20210930", "4", "3.2800", version="us-gaap/2023"),
        ],
        accessions=(ACC_10K,),
    )
    annual = _get(client, ACC_10K, "ANNUAL")
    assert annual["current"]["fact"]["qtrs"] == 4
    assert annual["comparative"]["fact"]["value"] == "5.6700"
    assert annual["change"]["transition"] == "POSITIVE_INCREASED"
    quarter = _get(client, ACC_10K, "QUARTER")
    assert quarter["current"]["status"] == "UNSUPPORTED_FORM_PERIOD"
    assert quarter["comparative"]["status"] == "CURRENT_UNAVAILABLE"


@pytest.mark.usefixtures("filings")
def test_an_amendment_resolves_independently_of_the_original(
    client: TestClient, db_session: Session, tmp_path: Path
) -> None:
    _ingest(
        db_session,
        tmp_path,
        [
            _eps(ACC_10Q, "20240331", "1", "1.5300"),
            _eps(ACC_10Q, "20230331", "1", "1.5200"),
            _eps(ACC_10QA, "20240331", "1", "1.5400"),
            _eps(ACC_10QA, "20230331", "1", "1.4900"),
        ],
    )
    assert _get(client, ACC_10Q)["comparative"]["fact"]["value"] == "1.5200"
    amendment = _get(client, ACC_10QA)
    assert amendment["form"] == "10-Q/A" and amendment["comparative"]["fact"]["value"] == "1.4900"


# ── read-only ────────────────────────────────────────────────────────────────────────────


@pytest.mark.usefixtures("filings")
def test_a_get_only_reads_it_issues_only_select_statements_and_changes_no_rows(
    client: TestClient, db_session: Session, tmp_path: Path
) -> None:
    _setup(db_session, tmp_path, "1.5300", "1.5200")
    tables = (FinancialFact, FinancialFactSubmission, FinancialFactArtifact, SecFiling)
    before = [db_session.query(table).count() for table in tables]
    statements: list[str] = []
    engine = db_session.get_bind()

    def capture(_conn: Any, _cursor: Any, statement: str, *_a: Any) -> None:
        statements.append(statement.lstrip().split(None, 1)[0].upper())

    event.listen(engine, "before_cursor_execute", capture)
    try:
        _get(client, ACC_10Q)
        _get(client, "0000111111-24-000001")
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert statements and set(statements) == {"SELECT"}, set(statements)
    assert [db_session.query(table).count() for table in tables] == before
    assert not db_session.new and not db_session.dirty and not db_session.deleted
    # a stored filing's acceptance time cannot influence the response
    db_session.execute(sa.text("UPDATE sec_filing SET accepted_at = '2099-01-01T00:00:00Z'"))
    db_session.commit()
    again = _get(client, ACC_10Q)
    assert again["change"]["eps_change"] == "0.0100"
    assert "2099" not in json.dumps(again)


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
        raise OperationalError("SELECT", {}, Exception("password=sup3rs3cret"))

    monkeypatch.setattr(filing_eps, "extract_comparative_eps_observation", boom)
    response = client.get(
        f"/api/v1/filings/{ACC_10Q}/eps", params={"period_class": "QUARTER", "eps_kind": "DILUTED"}
    )
    assert response.status_code == 503
    assert response.json() == {"detail": "Database unavailable."}
    assert "sup3rs3cret" not in response.text
    assert logged == ["Filing EPS read failed: OperationalError"]
