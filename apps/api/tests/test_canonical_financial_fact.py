"""The canonical financial-fact contract: source-agnostic, exact, no semantics invented."""

from __future__ import annotations

import dataclasses
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from quantforge.ingestion.canonical import CanonicalFactSubmission, CanonicalFinancialFact
from quantforge.models.enums import SecFormType
from quantforge.repositories.financial_fact import is_extension_version

ACC = "0000320193-24-000010"


def _fact(**overrides: Any) -> CanonicalFinancialFact:
    fields: dict[str, Any] = {
        "accession_number": ACC,
        "tag": "EarningsPerShareDiluted",
        "taxonomy_version": "us-gaap/2024",
        "unit": "USD/shares",
        "period_end": date(2024, 3, 31),
        "duration_quarters": 1,
        "value": Decimal("1.5300"),
    }
    fields.update(overrides)
    return CanonicalFinancialFact(**fields)


def test_a_fact_carries_only_source_concept_fields() -> None:
    names = {f.name for f in dataclasses.fields(CanonicalFinancialFact)}
    assert names == {
        "accession_number",
        "tag",
        "taxonomy_version",
        "unit",
        "period_end",
        "duration_quarters",
        "value",
        "segments",
        "coreg",
    }
    # Nothing semantic and nothing about availability.
    assert not names & {
        "eps",
        "revenue",
        "fiscal_quarter",
        "observed_at",
        "available_at",
        "accepted_at",
    }


@pytest.mark.parametrize(
    "value",
    [
        Decimal("0"),
        Decimal("-0.0001"),
        Decimal("1.50000"),
        Decimal("12345678901234567890.1234"),
        None,
    ],
)
def test_exact_values_that_fit_are_accepted(value: Decimal | None) -> None:
    assert _fact(value=value).value == value


@pytest.mark.parametrize(
    "value",
    [
        Decimal("1.23456"),
        Decimal("NaN"),
        Decimal("-Infinity"),
        Decimal("1" + "0" * 24),
        1.5,
        1,
        "1.5",
    ],
)
def test_values_that_would_round_or_are_not_exact_decimals_are_refused(value: Any) -> None:
    with pytest.raises(ValueError, match="value"):
        _fact(value=value)


@pytest.mark.parametrize(
    "overrides",
    [
        {"accession_number": "000032019324000010"},
        {"tag": "1Bad"},
        {"tag": ""},
        {"taxonomy_version": ""},
        {"taxonomy_version": "us gaap"},
        {"unit": ""},
        {"unit": " USD"},
        {"period_end": date(1899, 12, 31)},
        {"duration_quarters": -1},
        {"duration_quarters": True},
        {"duration_quarters": 1.0},
        {"segments": "x" * 1025},
        {"coreg": "x" * 257},
        {"coreg": "\x00"},
        {"segments": "é" * 1000, "coreg": "é" * 250},
    ],
)
def test_invalid_facts_are_refused(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match=r"must|cannot|longer|XBRL|taxonomy|outside|bytes"):
        _fact(**overrides)


def test_identity_components_distinguish_facts() -> None:
    base = _fact()
    assert base != _fact(duration_quarters=2)
    assert base != _fact(taxonomy_version="us-gaap/2023")
    assert base != _fact(unit="USD")
    assert base != _fact(segments="ProductOrServiceAxis=IPhoneMember;")
    assert base != _fact(coreg="GuarantorSubsidiaryMember")


def _submission(**overrides: Any) -> CanonicalFactSubmission:
    fields: dict[str, Any] = {
        "accession_number": ACC,
        "cik": "0000320193",
        "form": SecFormType.FORM_10Q,
        "filing_date": date(2024, 5, 3),
        "period_end": date(2024, 3, 31),
        "coregistrant_count": 1,
        "source_stated_accepted": "2024-05-03 16:30:22",
    }
    fields.update(overrides)
    return CanonicalFactSubmission(**fields)


def test_a_submission_keeps_the_sources_acceptance_text_unparsed() -> None:
    submission = _submission()
    assert submission.source_stated_accepted == "2024-05-03 16:30:22"
    assert isinstance(submission.source_stated_accepted, str)


@pytest.mark.parametrize(
    "overrides",
    [
        {"cik": "320193"},
        {"form": "10-Q"},
        {"coregistrant_count": 0},
        {"fiscal_year": 1800},
        {"fiscal_period": "q2"},
        {"fiscal_year_end": "930"},
        {"source_stated_accepted": "2024-05-03T16:30:22Z"},
    ],
)
def test_invalid_submissions_are_refused(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="must"):
        _submission(**overrides)


def test_extension_versions_are_accessions_and_standard_versions_are_not() -> None:
    assert is_extension_version("0000320193-24-000010")
    assert not is_extension_version("us-gaap/2024")
    assert not is_extension_version("ifrs-full/2023")


def test_dimension_and_coregistrant_text_may_be_non_ascii_within_the_index_byte_bound() -> None:
    fact = _fact(coreg="Société Générale SA", segments="Axis=Member;")
    assert fact.coreg == "Société Générale SA"
    # ...but a concept tag stays an ASCII local name.
    with pytest.raises(ValueError, match="tag"):
        _fact(tag="Ünicode")
