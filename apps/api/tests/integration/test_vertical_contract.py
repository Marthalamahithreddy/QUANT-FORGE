"""The descriptive accounting vertical, end to end over real stored rows and real HTTP.

stored filing -> FSDS submission/facts -> filings index -> (registrant-qualified) EPS detail ->
current EPS -> same-accession comparative -> descriptive change -> API JSON.

The JSON the API really returns for a coherent scenario is pinned in
``apps/web/tests/contract/vertical.json``. This test fails when the backend drifts from it; the
frontend test parses and renders the very same file, so a change on either side of the wire is
caught by both suites. Regenerate deliberately with ``UPDATE_CONTRACT_FIXTURES=1``.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from decimal import ROUND_HALF_EVEN, Context, Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session
from tests.vertical_scenario import (
    ACC_ANNUAL,
    ACC_HAPPY,
    ACC_MISMATCH,
    ACC_MISSING_CURRENT,
    ACC_NEGATIVE,
    ACC_NO_CIK,
    ACC_NULL_COMPARATIVE,
    ACC_NULL_CURRENT,
    ACC_TINY,
    ACC_UNKNOWN,
    ACC_ZERO,
    CIK_A,
    CIK_B,
    SPECS,
    seed_scenario,
)

from quantforge.core.config import Settings
from quantforge.db.session import get_session
from quantforge.main import create_app

pytestmark = pytest.mark.integration

GOLDEN = Path(__file__).resolve().parents[3] / "web" / "tests" / "contract" / "vertical.json"
FORBIDDEN = {
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
    "api_key",
    "user_agent",
    "contact",
}
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
ENUM_SCHEMAS = (
    "EpsStatus",
    "ComparativeEpsStatus",
    "EpsChangeStatus",
    "EpsTransition",
    "PercentageUnavailableReason",
    "EpsPeriodClass",
    "EpsKind",
    "SecFormType",
)

#: (accession, cik, period_class, eps_kind): every request the vertical exercises.
REQUESTS: tuple[tuple[str, str | None, str, str], ...] = (
    (ACC_HAPPY, CIK_A, "QUARTER", "DILUTED"),
    (ACC_HAPPY, CIK_B, "QUARTER", "DILUTED"),
    (ACC_HAPPY, None, "QUARTER", "DILUTED"),  # joint accession, unqualified
    (ACC_HAPPY, "0000999999", "QUARTER", "DILUTED"),  # well-formed unrelated CIK
    (ACC_HAPPY, CIK_A, "QUARTER", "BASIC_AND_DILUTED"),  # explicit kind, no fallback
    (ACC_HAPPY, CIK_A, "ANNUAL", "DILUTED"),  # a 10-Q does not support ANNUAL
    (ACC_MISMATCH, CIK_A, "QUARTER", "DILUTED"),
    (ACC_ANNUAL, CIK_A, "ANNUAL", "DILUTED"),
    (ACC_ZERO, CIK_A, "QUARTER", "DILUTED"),
    (ACC_NEGATIVE, CIK_A, "QUARTER", "DILUTED"),
    (ACC_NULL_COMPARATIVE, CIK_A, "QUARTER", "DILUTED"),
    (ACC_MISSING_CURRENT, CIK_A, "QUARTER", "DILUTED"),
    (ACC_NO_CIK, None, "QUARTER", "DILUTED"),
    (ACC_TINY, CIK_A, "QUARTER", "DILUTED"),
    (ACC_NULL_CURRENT, CIK_A, "QUARTER", "DILUTED"),
    (ACC_UNKNOWN, None, "QUARTER", "DILUTED"),
)


@pytest.fixture
def client(db_session: Session) -> Iterator[TestClient]:
    application: FastAPI = create_app(Settings(_env_file=None))
    application.dependency_overrides[get_session] = lambda: db_session
    with TestClient(application) as test_client:
        yield test_client


def _normalise(node: Any) -> Any:
    """Replace the random fact ids with stable placeholders, one per distinct id.

    Distinct ids stay distinct (numbered by first appearance), so the pinned JSON still tells two
    facts apart and would reveal a response that carried another registrant's fact.
    """
    ids: dict[str, str] = {}

    def placeholder(match: re.Match[str]) -> str:
        return ids.setdefault(match.group(0), f"00000000-0000-4000-8000-{len(ids) + 1:012d}")

    return json.loads(UUID.sub(placeholder, json.dumps(node)))


def _eps(client: TestClient, request: tuple[str, str | None, str, str]) -> dict[str, Any]:
    accession, cik, period_class, eps_kind = request
    params = {"period_class": period_class, "eps_kind": eps_kind}
    if cik is not None:
        params["cik"] = cik
    response = client.get(f"/api/v1/filings/{accession}/eps", params=params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _keys(node: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            found |= {key} | _keys(value)
    elif isinstance(node, list):
        for item in node:
            found |= _keys(item)
    return found


@pytest.fixture
def vertical(client: TestClient, db_session: Session) -> dict[str, Any]:
    seed_scenario(db_session)
    index = client.get("/api/v1/filings", params={"limit": 50})
    assert index.status_code == 200
    spec = client.get("/openapi.json").json()["components"]["schemas"]
    pinned: dict[str, Any] = _normalise(
        {
            "enums": {name: sorted(spec[name]["enum"]) for name in ENUM_SCHEMAS},
            "index": index.json(),
            "eps": [
                {
                    "request": {"accession": a, "cik": c, "period_class": p, "eps_kind": k},
                    "response": _eps(client, (a, c, p, k)),
                }
                for a, c, p, k in REQUESTS
            ],
        }
    )
    return pinned


def _by(vertical: dict[str, Any], accession: str, cik: str | None, kind: str = "DILUTED") -> Any:
    for entry in vertical["eps"]:
        r = entry["request"]
        if (r["accession"], r["cik"], r["eps_kind"]) == (accession, cik, kind):
            return entry["response"]
    raise AssertionError((accession, cik, kind))


def test_the_pinned_cross_layer_contract_matches_what_the_api_returns(
    vertical: dict[str, Any],
) -> None:
    if os.environ.get("UPDATE_CONTRACT_FIXTURES") == "1":
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps(vertical, indent=2) + "\n", encoding="utf-8")
        pytest.fail(
            "contract fixture regenerated: review the diff and re-run WITHOUT "
            "UPDATE_CONTRACT_FIXTURES so the comparison actually runs"
        )
    pinned = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert vertical == pinned, (
        "the API no longer returns the pinned vertical contract; the frontend parses and renders "
        "that same file. If the change is intended, regenerate with UPDATE_CONTRACT_FIXTURES=1 and "
        "review the diff."
    )


def test_the_index_lists_every_seeded_filing_once_per_registrant_in_a_stable_order(
    vertical: dict[str, Any],
) -> None:
    index = vertical["index"]
    assert index["total"] == len(SPECS) == len(index["items"])
    pairs = [(i["accession"], i["company_cik"]) for i in index["items"]]
    assert len(set(pairs)) == len(pairs), "no duplicated logical row"
    assert (ACC_HAPPY, CIK_A) in pairs and (ACC_HAPPY, CIK_B) in pairs, "joint rows are both kept"
    assert (ACC_NO_CIK, None) in pairs, "a CIK-less row stays visible"
    dates = [i["filing_date"] for i in index["items"]]
    assert dates == sorted(dates, reverse=True)
    for item in index["items"]:
        assert item["fsds_period_end"] is not None and item["report_date"] is None


def test_identity_survives_index_row_to_request_to_response(
    client: TestClient, vertical: dict[str, Any]
) -> None:
    """Every index row, followed the way the frontend link does, returns that same filing."""
    for row in vertical["index"]["items"]:
        period = "ANNUAL" if row["form"].startswith("10-K") else "QUARTER"
        body = _eps(client, (row["accession"], row["company_cik"], period, "DILUTED"))
        assert body["accession"] == row["accession"]
        assert body["company_cik"] == row["company_cik"]
        assert body["form"] == row["form"], "the registrant's own filing, not another's"
        assert body["current"]["status"] != "NOT_INGESTED"
        fact = body["current"]["fact"]
        assert fact is None or fact["period_end"] == row["fsds_period_end"]


def test_the_happy_chain_matches_the_seeded_facts_and_the_domain_arithmetic(
    vertical: dict[str, Any],
) -> None:
    a = _by(vertical, ACC_HAPPY, CIK_A)
    assert a["current"]["fact"]["value"] == "1.5300"
    assert a["current"]["fact"]["period_end"] == "2024-03-31"
    assert a["comparative"]["fact"]["value"] == "1.5200"
    assert a["comparative"]["fact"]["period_end"] == "2023-03-31"
    assert a["change"]["eps_change"] == "0.0100"
    assert a["change"]["transition"] == "POSITIVE_INCREASED"
    expected = Context(prec=50, rounding=ROUND_HALF_EVEN).divide(
        Decimal("0.0100"), Decimal("1.5200")
    )
    assert a["change"]["percentage_change"] == format(expected, "f")
    assert a["descriptive_only"] is True and a["no_point_in_time_claim"] is True


def test_a_joint_accession_resolves_per_registrant_and_stays_ambiguous_unqualified(
    vertical: dict[str, Any],
) -> None:
    a = _by(vertical, ACC_HAPPY, CIK_A)
    b = _by(vertical, ACC_HAPPY, CIK_B)
    assert (a["company_cik"], b["company_cik"]) == (CIK_A, CIK_B)
    assert a["current"]["fact"]["value"] == "1.5300" and b["current"]["fact"]["value"] == "4.0000"
    assert b["change"]["eps_change"] == "1.0000"
    assert b["change"]["percentage_change"] == "0." + "3" * 50, "1/3 at 50 digits, never rounded"
    unqualified = _by(vertical, ACC_HAPPY, None)
    assert unqualified["current"]["status"] == "AMBIGUOUS"
    assert unqualified["comparative"]["status"] == "CURRENT_UNAVAILABLE"
    assert unqualified["change"]["status"] == "INPUT_UNAVAILABLE"


def test_a_null_cik_row_is_accession_only_and_resolves_without_inventing_identity(
    vertical: dict[str, Any],
) -> None:
    body = _by(vertical, ACC_NO_CIK, None)
    assert body["company_cik"] is None and body["current"]["status"] == "AVAILABLE"
    assert body["current"]["fact"]["value"] == "2.0000"


def test_a_wrong_cik_is_a_qualified_not_ingested_and_never_a_fallback(
    vertical: dict[str, Any],
) -> None:
    body = _by(vertical, ACC_HAPPY, "0000999999")
    assert body["current"]["status"] == "NOT_INGESTED" and body["form"] is None
    assert body["company_cik"] == "0000999999" and body["current"]["fact"] is None
    assert body["comparative"]["status"] == "CURRENT_UNAVAILABLE"
    assert body["change"]["status"] == "INPUT_UNAVAILABLE"
    unknown = _by(vertical, ACC_UNKNOWN, None)
    assert unknown["current"]["status"] == "NOT_INGESTED" and unknown["company_cik"] is None


def test_a_current_refusal_propagates_down_the_chain_without_a_value(
    vertical: dict[str, Any],
) -> None:
    body = _by(vertical, ACC_MISSING_CURRENT, CIK_A)
    assert body["current"]["status"] == "MISSING" and body["current"]["fact"] is None
    assert body["comparative"]["status"] == "CURRENT_UNAVAILABLE"
    assert "MISSING" in body["comparative"]["reason"], "the cause is carried, not collapsed"
    assert body["change"] == {
        "status": "INPUT_UNAVAILABLE",
        "eps_change": None,
        "transition": None,
        "percentage_change": None,
        "percentage_semantics": "ratio",
        "percentage_unavailable_reason": None,
    }
    assert _by(vertical, ACC_HAPPY, CIK_A, "BASIC_AND_DILUTED")["current"]["status"] == "MISSING"
    assert _by(vertical, ACC_HAPPY, CIK_A)["comparative"]["status"] == "AVAILABLE"


def test_the_comparative_period_mismatch_keeps_its_exact_status_and_reason(
    vertical: dict[str, Any],
) -> None:
    body = _by(vertical, ACC_MISMATCH, CIK_A)
    assert body["current"]["status"] == "AVAILABLE"
    assert body["comparative"]["status"] == "COMPARATIVE_PERIOD_MISMATCH"
    assert body["comparative"]["reason"] and body["comparative"]["fact"] is None
    assert body["change"]["status"] == "INPUT_UNAVAILABLE"


def test_null_values_are_distinct_from_missing_and_from_zero(vertical: dict[str, Any]) -> None:
    comparative_null = _by(vertical, ACC_NULL_COMPARATIVE, CIK_A)
    assert comparative_null["comparative"]["status"] == "COMPARATIVE_NULL_VALUE"
    assert comparative_null["comparative"]["fact"]["value"] is None
    assert comparative_null["change"]["status"] == "INPUT_UNAVAILABLE"
    current_null = _by(vertical, ACC_NULL_CURRENT, CIK_A)
    assert current_null["current"]["status"] == "NULL_VALUE"
    assert current_null["current"]["fact"]["value"] is None
    assert current_null["comparative"]["status"] == "CURRENT_UNAVAILABLE"
    zero = _by(vertical, ACC_ZERO, CIK_A)
    assert zero["comparative"]["status"] == "AVAILABLE"
    assert zero["comparative"]["fact"]["value"] == "0.0000", "a stored zero is a value, not null"
    missing = _by(vertical, ACC_MISSING_CURRENT, CIK_A)["current"]["status"]
    assert (
        len({comparative_null["comparative"]["status"], current_null["current"]["status"], missing})
        == 3
    )


def test_zero_negative_and_profit_to_loss_follow_the_domain_policy(
    vertical: dict[str, Any],
) -> None:
    zero = _by(vertical, ACC_ZERO, CIK_A)["change"]
    assert zero["status"] == "AVAILABLE" and zero["transition"] == "ZERO_TO_PROFIT"
    assert zero["eps_change"] == "0.5000" and zero["percentage_change"] is None
    assert zero["percentage_unavailable_reason"] == "ZERO_COMPARATIVE"
    negative = _by(vertical, ACC_NEGATIVE, CIK_A)["change"]
    assert negative["transition"] == "LOSS_NARROWED" and negative["eps_change"] == "0.5000"
    assert negative["percentage_change"] is None
    assert negative["percentage_unavailable_reason"] == "NEGATIVE_COMPARATIVE"
    loss = _by(vertical, ACC_ANNUAL, CIK_A)
    assert loss["change"]["transition"] == "PROFIT_TO_LOSS"
    assert (
        loss["change"]["eps_change"] == "-1.5000" and loss["change"]["percentage_change"] == "-1.5"
    )
    assert loss["period_class"] == "ANNUAL" and loss["form"] == "10-K"


def test_exact_decimals_survive_the_wire_as_fixed_point_strings(vertical: dict[str, Any]) -> None:
    tiny = _by(vertical, ACC_TINY, CIK_A)
    assert tiny["current"]["fact"]["value"] == "100.0001"
    assert tiny["change"]["eps_change"] == "0.0001"
    assert tiny["change"]["percentage_change"] == "0.000001", "no exponent, no rounding"
    seen: list[str] = []
    for entry in vertical["eps"]:
        body = entry["response"]
        for value in (
            body["current"]["fact"] and body["current"]["fact"]["value"],
            body["comparative"]["fact"] and body["comparative"]["fact"]["value"],
            body["change"]["eps_change"],
            body["change"]["percentage_change"],
        ):
            if value is not None:
                assert isinstance(value, str) and not re.search("[eE]", value), value
                seen.append(value)
    assert {"1.5300", "-0.5000", "0.0000", "0.000001"} <= set(seen)


def test_no_public_filing_response_exposes_availability_or_internal_fields(
    vertical: dict[str, Any],
) -> None:
    assert not _keys(vertical["index"]) & FORBIDDEN
    for entry in vertical["eps"]:
        assert not _keys(entry["response"]) & FORBIDDEN
    text = json.dumps(vertical).lower()
    for phrase in ("accepted", "available at", "known at", "tradable then", "point-in-time safe"):
        assert phrase not in text, phrase


def test_the_whole_vertical_reads_with_select_only(
    client: TestClient, db_session: Session, vertical: dict[str, Any]
) -> None:
    engine = db_session.get_bind()
    statements: list[str] = []

    def capture(_c: Any, _cur: Any, statement: str, *_a: Any) -> None:
        statements.append(statement.lstrip().split(None, 1)[0].upper())

    event.listen(engine, "before_cursor_execute", capture)
    try:
        client.get("/api/v1/filings", params={"limit": 50})
        for request in REQUESTS:
            _eps(client, request)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    statements = [s for s in statements if s not in ("SAVEPOINT", "RELEASE", "ROLLBACK")]
    assert statements and set(statements) == {"SELECT"}, set(statements)
    assert not db_session.new and not db_session.dirty and not db_session.deleted
