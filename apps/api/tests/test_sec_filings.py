"""Unit tests for the SEC submissions mapper (identity only) and the canonical filing.

No network and no database. The submissions-JSON acceptance value is never an instant (step 11.2H);
see ``test_sec_identity_without_acceptance`` for the real-evidence regressions.
"""

from __future__ import annotations

import ast
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from quantforge.ingestion.canonical import CanonicalSecFiling
from quantforge.models.enums import AvailabilityBasis, SecFormType
from quantforge.models.filing import ITEMS_LENGTH, PRIMARY_DOCUMENT_LENGTH
from quantforge.providers.errors import ProviderPayloadError
from quantforge.providers.sec import mapper as mapper_module
from quantforge.providers.sec.mapper import map_recent_filings
from quantforge.research.availability import APPROVED_AVAILABILITY_BASES

_CIK = "0000320193"


def _utc(
    year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0
) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=UTC)


# ── the corrected instant is acceptance metadata, never availability ────────


def test_correcting_the_acceptance_instant_approves_nothing_for_point_in_time_use() -> None:
    assert AvailabilityBasis.SEC_FILING_ACCEPTANCE not in APPROVED_AVAILABILITY_BASES
    assert set(APPROVED_AVAILABILITY_BASES) == {AvailabilityBasis.SOURCE_PUBLICATION}


def test_the_mapper_and_canonical_filing_carry_no_availability_or_execution_field() -> None:
    tree = ast.parse(Path(mapper_module.__file__ or "").read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    } | {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert not {name for name in imported if "availability" in name or "research" in name}
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    assert not names & {"AvailabilityBasis", "observed_at", "observed_at_basis", "known_as_of"}
    assert not set(CanonicalSecFiling.__dataclass_fields__) & {
        "observed_at",
        "observed_at_basis",
        "filing_public_at",
        "known_as_of",
    }


# ── the mapper ──────────────────────────────────────────────────────────────


def _document(**columns: list[object]) -> dict[str, object]:
    base: dict[str, list[object]] = {
        "accessionNumber": [
            "0000320193-24-000010",
            "0000320193-24-000011",
            "0000320193-24-000012",
            "0000320193-24-000013",
            "0000320193-24-000014",
        ],
        "form": ["10-Q", "8-K", "4", "10-K/A", "10-K"],
        "filingDate": ["2024-05-03", "2024-05-02", "2024-05-01", "2024-12-02", "2024-11-01"],
        "reportDate": ["2024-03-30", "2024-05-02", "", "2024-09-28", "2024-09-28"],
        "acceptanceDateTime": [
            "2024-05-03T20:30:22.000Z",
            "2024-05-02T20:31:00.000Z",
            "2024-05-01T20:00:00.000Z",
            "2024-12-02T14:15:00.000Z",
            "2024-11-01T10:01:36.000Z",
        ],
        "primaryDocument": ["q.htm", "k8.htm", "x.xml", "ka.htm", "k.htm"],
        "items": ["", "2.02,9.01", "", "", ""],
    }
    base.update(columns)
    return {"cik": "320193", "filings": {"recent": base}}


def test_supported_forms_are_mapped_and_others_are_skipped() -> None:
    filings, rejected = map_recent_filings(_document(), cik=_CIK)

    assert rejected == []
    assert [f.form for f in filings] == [
        SecFormType.FORM_8K,
        SecFormType.FORM_10Q,
        SecFormType.FORM_10K,
        SecFormType.FORM_10K_A,
    ]  # Form 4 is not supported; filing-date order
    assert all(f.cik == _CIK for f in filings)


def test_an_8k_is_just_a_filing_with_its_item_codes_preserved_verbatim() -> None:
    filings, _ = map_recent_filings(_document(), cik=_CIK)
    eight_k = next(f for f in filings if f.form is SecFormType.FORM_8K)

    assert eight_k.items == "2.02,9.01"
    assert not hasattr(eight_k, "reported_eps")
    assert not hasattr(eight_k, "observed_at")


def test_an_amendment_is_a_separate_accession_with_its_own_filing_date() -> None:
    filings, _ = map_recent_filings(_document(), cik=_CIK)
    original = next(f for f in filings if f.form is SecFormType.FORM_10K)
    amendment = next(f for f in filings if f.form is SecFormType.FORM_10K_A)

    assert original.accession_number != amendment.accession_number
    assert amendment.filing_date > original.filing_date
    assert amendment.accepted_at is None and original.accepted_at is None
    assert amendment.form.is_amendment and not original.form.is_amendment


def test_dates_are_dates_and_an_empty_report_date_is_none() -> None:
    filings, _ = map_recent_filings(_document(), cik=_CIK)
    eight_k = next(f for f in filings if f.form is SecFormType.FORM_8K)
    quarterly = next(f for f in filings if f.form is SecFormType.FORM_10Q)

    assert quarterly.filing_date == date(2024, 5, 3)
    assert quarterly.report_date == date(2024, 3, 30)
    assert eight_k.report_date == date(2024, 5, 2)
    assert quarterly.primary_document == "q.htm"
    assert type(quarterly.filing_date) is date

    blank, _ = map_recent_filings(
        _document(reportDate=["", "", "", "", ""], items=["", "", "", "", ""]), cik=_CIK
    )
    assert all(f.report_date is None and f.items is None for f in blank)


@pytest.mark.parametrize(
    "document",
    [
        [],
        {"cik": "320193", "filings": {}},
        {"cik": "320193", "filings": []},
        {"cik": "320193", "filings": "x"},
        {"cik": "320193", "filings": {"recent": []}},
        {"cik": "320193", "filings": {"recent": {"form": []}}},
    ],
)
def test_a_malformed_envelope_is_a_payload_error(document: object) -> None:
    with pytest.raises(ProviderPayloadError):
        map_recent_filings(document, cik=_CIK)


def test_a_column_of_the_wrong_length_is_a_payload_error() -> None:
    with pytest.raises(ProviderPayloadError, match="length"):
        map_recent_filings(_document(form=["10-Q"]), cik=_CIK)


# ── the canonical filing contract ───────────────────────────────────────────

_GOOD = {
    "cik": _CIK,
    "accession_number": "0000320193-24-000010",
    "form": SecFormType.FORM_10Q,
    "filing_date": date(2024, 5, 3),
    "accepted_at": _utc(2024, 5, 3, 20, 30, 22),
}


def _filing(**overrides: object) -> CanonicalSecFiling:
    return CanonicalSecFiling(**{**_GOOD, **overrides})  # type: ignore[arg-type]


def test_a_valid_filing_is_accepted() -> None:
    assert _filing().form is SecFormType.FORM_10Q


@pytest.mark.parametrize("cik", ["320193", "032019300000", "ABCDEFGHIJ", ""])
def test_a_cik_must_be_exactly_ten_digits(cik: str) -> None:
    with pytest.raises(ValueError, match="cik"):
        _filing(cik=cik)


@pytest.mark.parametrize(
    "accession", ["000032019324000010", "0000320193-24-00001", "0000320193-2024-000010", ""]
)
def test_an_accession_must_be_dashed_ten_two_six(accession: str) -> None:
    with pytest.raises(ValueError, match="accession_number"):
        _filing(accession_number=accession)


def test_accepted_at_must_be_aware_utc() -> None:
    with pytest.raises(ValueError, match="UTC"):
        _filing(accepted_at=datetime(2024, 5, 3, 16, 30))  # noqa: DTZ001 - deliberately naive
    with pytest.raises(ValueError, match="UTC"):
        _filing(accepted_at=datetime(2024, 5, 3, 16, 30, tzinfo=timezone(timedelta(hours=-4))))


def test_form_and_dates_are_type_checked() -> None:
    with pytest.raises(ValueError, match="form"):
        _filing(form="10-Q")
    with pytest.raises(ValueError, match="plain date"):
        _filing(filing_date=datetime(2024, 5, 3, tzinfo=UTC))
    with pytest.raises(ValueError, match="plain date"):
        _filing(report_date=datetime(2024, 3, 30, tzinfo=UTC))
    with pytest.raises(ValueError, match="non-blank"):
        _filing(primary_document="  ")


def test_canonical_filing_respects_the_stored_limits() -> None:
    with pytest.raises(ValueError, match="predates EDGAR"):
        _filing(filing_date=date(1990, 1, 2))
    with pytest.raises(ValueError, match="stored limit"):
        _filing(primary_document="x" * 256)
    with pytest.raises(ValueError, match="stored limit"):
        _filing(items="9" * (ITEMS_LENGTH + 1))
    _filing(primary_document="x" * PRIMARY_DOCUMENT_LENGTH, items="9" * ITEMS_LENGTH)


def test_a_record_breaking_a_stored_limit_is_rejected_not_fatal_to_the_batch() -> None:
    document = _document(primaryDocument=["q.htm", "y" * 300, "x.xml", "ka.htm", "k.htm"])

    filings, rejected = map_recent_filings(document, cik=_CIK)

    assert len(rejected) == 1 and "0000320193-24-000011" in rejected[0]
    assert len(filings) == 3


def test_a_document_that_does_not_identify_its_registrant_is_refused() -> None:
    document = _document()
    del document["cik"]

    with pytest.raises(ProviderPayloadError, match="does not state its own"):
        map_recent_filings(document, cik=_CIK)


def test_a_document_for_another_registrant_is_refused() -> None:
    document = _document()
    document["cik"] = 789019

    with pytest.raises(ProviderPayloadError, match="wrong registrant"):
        map_recent_filings(document, cik=_CIK)


def test_a_filing_may_have_no_established_acceptance_instant() -> None:
    assert _filing(accepted_at=None).accepted_at is None
    assert (
        CanonicalSecFiling(
            cik=_CIK,
            accession_number="0000320193-24-000010",
            form=SecFormType.FORM_10Q,
            filing_date=date(2024, 5, 3),
        ).accepted_at
        is None
    )
