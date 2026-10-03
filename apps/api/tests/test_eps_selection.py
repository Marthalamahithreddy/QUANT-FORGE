"""Accession-scoped EPS selection: the pure rules, exhaustively, with no database.

Every refusal status, the quarter-vs-YTD and no-Q4 tripwires, dimension / co-registrant /
extension / continuing-operations handling, taxonomy-version independence, amendment independence,
order independence, exact Decimals and the structural no-availability / no-signal guarantees.
"""

from __future__ import annotations

import ast
import dataclasses
import itertools
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

import quantforge
from quantforge import financials
from quantforge.financials.eps import (
    EPS_CONCEPT_TAG,
    EPS_UNIT,
    EpsFactObservation,
    EpsInputFact,
    EpsKind,
    EpsPeriodClass,
    EpsStatus,
    select_eps_fact,
)
from quantforge.models.enums import SecFormType

ACC = "0000320193-24-000010"
ACC_AMENDMENT = "0000320193-24-000020"
PERIOD_END = date(2024, 3, 31)
PRIOR_YEAR_END = date(2023, 3, 31)
DILUTED = "EarningsPerShareDiluted"
BASIC = "EarningsPerShareBasic"
BASIC_AND_DILUTED = "EarningsPerShareBasicAndDiluted"
CONTINUING_OPS = "IncomeLossFromContinuingOperationsPerDilutedShare"
GAAP = "us-gaap/2024"
SRC = Path(quantforge.__file__).resolve().parent


def fact(**overrides: object) -> EpsInputFact:
    fields: dict[str, object] = {
        "fact_id": uuid.uuid4(),
        "accession_number": ACC,
        "tag": DILUTED,
        "taxonomy_version": GAAP,
        "unit": EPS_UNIT,
        "period_end": PERIOD_END,
        "duration_quarters": 1,
        "value": Decimal("1.5300"),
        "segments": "",
        "coreg": "",
    }
    fields.update(overrides)
    return EpsInputFact(**fields)  # type: ignore[arg-type]


def select(
    facts: list[EpsInputFact],
    *,
    form: SecFormType = SecFormType.FORM_10Q,
    period: EpsPeriodClass = EpsPeriodClass.QUARTER,
    kind: EpsKind = EpsKind.DILUTED,
    period_ends: tuple[date, ...] = (PERIOD_END,),
    accession: str = ACC,
) -> EpsFactObservation:
    return select_eps_fact(
        accession_number=accession,
        company_cik="0000320193",
        form=form,
        submission_period_ends=period_ends,
        facts=facts,
        period_class=period,
        eps_kind=kind,
    )


def assert_refused(observation: EpsFactObservation, status: EpsStatus) -> None:
    assert observation.status is status
    assert observation.value is None
    assert observation.fact_id is None
    assert observation.reason


# ── the happy paths ──────────────────────────────────────────────────────────────────────


def test_a_simple_quarter_is_available_with_the_exact_decimal() -> None:
    chosen = fact(value=Decimal("1.5300"))
    result = select([chosen])
    assert result.status is EpsStatus.AVAILABLE
    assert result.value == Decimal("1.5300")
    assert isinstance(result.value, Decimal)
    assert str(result.value) == "1.5300", "the source's digits, not a rounded or float rendering"
    assert result.fact_id == chosen.fact_id
    assert (result.tag, result.taxonomy_version, result.unit) == (DILUTED, GAAP, EPS_UNIT)
    assert (result.period_end, result.duration_quarters) == (PERIOD_END, 1)
    assert (result.segments, result.coreg) == ("", "")
    assert result.form is SecFormType.FORM_10Q
    assert result.period_class is EpsPeriodClass.QUARTER and result.eps_kind is EpsKind.DILUTED


def test_a_negative_and_a_high_precision_value_survive_exactly() -> None:
    for text in ("-0.0100", "123456789012.1234", "0.0000"):
        assert select([fact(value=Decimal(text))]).value == Decimal(text)


def test_an_annual_10k_is_available() -> None:
    result = select(
        [fact(duration_quarters=4, value=Decimal("6.1300"))],
        form=SecFormType.FORM_10K,
        period=EpsPeriodClass.ANNUAL,
    )
    assert result.status is EpsStatus.AVAILABLE and result.value == Decimal("6.1300")
    assert result.duration_quarters == 4


@pytest.mark.parametrize("ytd_quarters", [2, 3])
def test_a_quarter_request_never_selects_a_year_to_date_amount(ytd_quarters: int) -> None:
    quarter = fact(duration_quarters=1, value=Decimal("0.8000"))
    ytd = fact(duration_quarters=ytd_quarters, value=Decimal("1.5000"))
    for ordering in itertools.permutations([quarter, ytd]):
        result = select(list(ordering))
        assert result.status is EpsStatus.AVAILABLE
        assert result.value == Decimal("0.8000"), "the quarter, never the year-to-date amount"
        assert result.duration_quarters == 1


@pytest.mark.parametrize("ytd_quarters", [2, 3])
def test_a_year_to_date_only_accession_has_no_quarter_and_nothing_is_derived(
    ytd_quarters: int,
) -> None:
    assert_refused(
        select([fact(duration_quarters=ytd_quarters, value=Decimal("1.5000"))]),
        EpsStatus.MISSING,
    )


def test_there_is_no_q4_and_no_year_minus_ytd_derivation_anywhere() -> None:
    # The only period classes are QUARTER and ANNUAL; a 10-K quarter is refused, never derived.
    assert {c.value for c in EpsPeriodClass} == {"QUARTER", "ANNUAL"}
    ten_k_facts = [
        fact(duration_quarters=4, value=Decimal("6.1300")),
        fact(duration_quarters=3, value=Decimal("4.0000")),  # nine-month YTD in a hypothetical 10-K
        fact(duration_quarters=1, value=Decimal("2.1300")),  # a face-of-statement Q4 column
    ]
    refused = select(ten_k_facts, form=SecFormType.FORM_10K, period=EpsPeriodClass.QUARTER)
    assert_refused(refused, EpsStatus.UNSUPPORTED_FORM_PERIOD)
    annual = select(ten_k_facts, form=SecFormType.FORM_10K, period=EpsPeriodClass.ANNUAL)
    assert annual.value == Decimal("6.1300"), "ANNUAL is the stated figure, not 6.13 - 4.00"


@pytest.mark.parametrize(
    ("form", "period"),
    [
        (SecFormType.FORM_10Q, EpsPeriodClass.ANNUAL),
        (SecFormType.FORM_10Q_A, EpsPeriodClass.ANNUAL),
        (SecFormType.FORM_10K, EpsPeriodClass.QUARTER),
        (SecFormType.FORM_10K_A, EpsPeriodClass.QUARTER),
        (SecFormType.FORM_8K, EpsPeriodClass.QUARTER),
        (SecFormType.FORM_8K_A, EpsPeriodClass.ANNUAL),
    ],
)
def test_unsupported_form_and_period_combinations_are_refused(
    form: SecFormType, period: EpsPeriodClass
) -> None:
    assert_refused(
        select([fact(duration_quarters=1), fact(duration_quarters=4)], form=form, period=period),
        EpsStatus.UNSUPPORTED_FORM_PERIOD,
    )


@pytest.mark.parametrize(
    ("form", "period", "quarters"),
    [
        (SecFormType.FORM_10Q, EpsPeriodClass.QUARTER, 1),
        (SecFormType.FORM_10Q_A, EpsPeriodClass.QUARTER, 1),
        (SecFormType.FORM_10K, EpsPeriodClass.ANNUAL, 4),
        (SecFormType.FORM_10K_A, EpsPeriodClass.ANNUAL, 4),
    ],
)
def test_the_four_supported_form_period_pairs_are_available(
    form: SecFormType, period: EpsPeriodClass, quarters: int
) -> None:
    result = select([fact(duration_quarters=quarters)], form=form, period=period)
    assert result.status is EpsStatus.AVAILABLE and result.form is form


# ── period end ───────────────────────────────────────────────────────────────────────────


def test_a_prior_year_comparative_is_never_selected_for_the_filings_period() -> None:
    current = fact(value=Decimal("1.5300"))
    comparative = fact(period_end=PRIOR_YEAR_END, value=Decimal("1.5200"))
    for ordering in itertools.permutations([current, comparative]):
        assert select(list(ordering)).value == Decimal("1.5300")


def test_only_a_comparative_period_is_missing_not_selected() -> None:
    assert_refused(
        select([fact(period_end=PRIOR_YEAR_END, value=Decimal("1.5200"))]), EpsStatus.MISSING
    )


def test_submissions_stating_different_period_ends_are_ambiguous() -> None:
    result = select([fact()], period_ends=(PERIOD_END, date(2024, 4, 30)))
    assert_refused(result, EpsStatus.AMBIGUOUS)


def test_no_ingested_submission_is_not_ingested_and_not_missing() -> None:
    result = select([], period_ends=())
    assert_refused(result, EpsStatus.NOT_INGESTED)
    assert "not evidence" in result.reason


def test_an_ingested_accession_without_eps_is_missing_not_not_ingested() -> None:
    assert_refused(select([]), EpsStatus.MISSING)


# ── concept: diluted, basic, basic-and-diluted, continuing operations ────────────────────


def test_basic_eps_is_never_substituted_for_diluted() -> None:
    assert_refused(select([fact(tag=BASIC, value=Decimal("1.6000"))]), EpsStatus.MISSING)


def test_continuing_operations_eps_is_never_substituted_for_diluted() -> None:
    assert_refused(select([fact(tag=CONTINUING_OPS)]), EpsStatus.MISSING)


def test_a_tag_that_merely_contains_the_name_is_not_matched() -> None:
    for tag in ("EarningsPerShareDilutedRestated", "EarningsPerShareDiluted2", "earningspershare"):
        assert_refused(select([fact(tag=tag)]), EpsStatus.MISSING)


def test_basic_and_diluted_is_a_separately_requested_kind_never_a_fallback() -> None:
    both = fact(tag=BASIC_AND_DILUTED, value=Decimal("2.0000"))
    requested = select([both], kind=EpsKind.BASIC_AND_DILUTED)
    assert requested.status is EpsStatus.AVAILABLE
    assert requested.eps_kind is EpsKind.BASIC_AND_DILUTED, "labelled as what it is"
    assert requested.tag == BASIC_AND_DILUTED and requested.value == Decimal("2.0000")
    # Diluted requested while only the combined concept exists: no automatic fallback.
    assert_refused(select([both], kind=EpsKind.DILUTED), EpsStatus.MISSING)
    # Combined requested while only diluted exists: no automatic fallback either way.
    assert_refused(select([fact()], kind=EpsKind.BASIC_AND_DILUTED), EpsStatus.MISSING)


def test_with_both_concepts_present_each_kind_returns_only_its_own() -> None:
    diluted = fact(tag=DILUTED, value=Decimal("1.5000"))
    combined = fact(tag=BASIC_AND_DILUTED, value=Decimal("1.6000"))
    assert select([diluted, combined], kind=EpsKind.DILUTED).value == Decimal("1.5000")
    assert select([combined, diluted], kind=EpsKind.BASIC_AND_DILUTED).value == Decimal("1.6000")


def test_the_concept_tags_are_the_exact_standard_names() -> None:
    assert EPS_CONCEPT_TAG == {
        EpsKind.DILUTED: DILUTED,
        EpsKind.BASIC_AND_DILUTED: BASIC_AND_DILUTED,
    }


# ── taxonomy version / extensions ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "version", ["us-gaap/2009", "us-gaap/2012", "us-gaap/2018", "us-gaap/2023", "us-gaap/2024"]
)
def test_any_historical_us_gaap_release_is_recognised(version: str) -> None:
    result = select([fact(taxonomy_version=version)])
    assert result.status is EpsStatus.AVAILABLE and result.taxonomy_version == version


@pytest.mark.parametrize(
    "version", ["ifrs-full/2023", "dei/2024", "us-gaap-2024", "us-gaap/24", "us-gaap/20245", ""]
)
def test_a_version_that_is_not_a_us_gaap_release_is_not_us_gaap(version: str) -> None:
    assert_refused(select([fact(taxonomy_version=version)]), EpsStatus.MISSING)


def test_two_us_gaap_releases_in_one_accession_are_ambiguous_even_with_equal_values() -> None:
    first = fact(taxonomy_version="us-gaap/2023", value=Decimal("1.5300"))
    second = fact(taxonomy_version="us-gaap/2024", value=Decimal("1.5300"))
    results = {select(list(o)).reason for o in itertools.permutations([first, second])}
    assert len(results) == 1, "the reason does not depend on the supplied order"
    result = select([first, second])
    assert_refused(result, EpsStatus.AMBIGUOUS)
    assert "us-gaap/2023, us-gaap/2024" in result.reason


def test_an_extension_with_the_standard_name_is_never_mapped() -> None:
    extension = fact(taxonomy_version="0000320193-24-000010", value=Decimal("9.9900"))
    assert_refused(select([extension]), EpsStatus.EXTENSION_ONLY)


def test_an_extension_next_to_a_standard_fact_is_ignored_not_ambiguous() -> None:
    standard = fact(value=Decimal("1.5300"))
    extension = fact(taxonomy_version="0000320193-24-000010", value=Decimal("9.9900"))
    for ordering in itertools.permutations([standard, extension]):
        result = select(list(ordering))
        assert result.status is EpsStatus.AVAILABLE and result.value == Decimal("1.5300")


def test_an_eps_like_custom_name_is_not_detected_or_mapped() -> None:
    # No name heuristics: a differently named extension is simply not the requested concept.
    custom = fact(tag="AdjustedDilutedEps", taxonomy_version="0000320193-24-000010")
    assert_refused(select([custom]), EpsStatus.MISSING)


# ── unit ─────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "unit", ["CAD", "EUR", "shares", "pure", "USD/shares", "USD/share", "usd", "USD "]
)
def test_a_wrong_unit_is_refused_even_when_the_tag_matches(unit: str) -> None:
    result = select([fact(unit=unit)])
    assert_refused(result, EpsStatus.WRONG_UNIT)
    assert unit in result.reason


def test_a_wrong_unit_on_the_entity_wide_fact_is_not_hidden_by_right_unit_variants() -> None:
    wrong_plain = fact(unit="CAD", value=Decimal("153.0000"))
    class_a = fact(segments="ClassAxis=AMember;", value=Decimal("9.1"))
    coreg = fact(coreg="GuarantorSubsidiaryMember", value=Decimal("9.2"))
    for ordering in itertools.permutations([wrong_plain, class_a, coreg]):
        result = select(list(ordering))
        assert_refused(result, EpsStatus.WRONG_UNIT)
        assert "CAD" in result.reason


def test_the_right_unit_on_a_sibling_wins_over_a_wrong_unit_fact() -> None:
    right = fact(value=Decimal("1.5300"))
    wrong = fact(unit="CAD", value=Decimal("153.0000"))
    for ordering in itertools.permutations([right, wrong]):
        assert select(list(ordering)).value == Decimal("1.5300")


def test_the_unit_is_the_bare_usd_currency_code_never_usd_per_shares() -> None:
    assert EPS_UNIT == "USD"
    assert select([fact(unit="USD")]).status is EpsStatus.AVAILABLE
    assert_refused(select([fact(unit="USD/shares")]), EpsStatus.WRONG_UNIT)


def test_a_usd_fact_of_another_concept_is_never_selected_just_because_it_is_usd() -> None:
    for tag in ("NetIncomeLoss", "Revenues", "Assets", "CommonStockDividendsPerShareDeclared"):
        assert_refused(select([fact(tag=tag, unit="USD")]), EpsStatus.MISSING)


def test_cad_eps_is_wrong_unit_for_both_kinds_and_is_never_converted() -> None:
    for kind, tag in ((EpsKind.DILUTED, DILUTED), (EpsKind.BASIC_AND_DILUTED, BASIC_AND_DILUTED)):
        result = select([fact(tag=tag, unit="CAD", value=Decimal("1.2300"))], kind=kind)
        assert_refused(result, EpsStatus.WRONG_UNIT)


def test_usd_basic_and_diluted_is_available_only_when_that_kind_is_requested() -> None:
    both = fact(tag=BASIC_AND_DILUTED, unit="USD", value=Decimal("2.0000"))
    assert select([both], kind=EpsKind.BASIC_AND_DILUTED).status is EpsStatus.AVAILABLE
    assert_refused(select([both], kind=EpsKind.DILUTED), EpsStatus.MISSING)


def test_real_shaped_multi_axis_segments_and_a_real_shaped_coreg_stay_fail_closed() -> None:
    segments = "ClassOfStock=CommonClassA;ConsolidatedEntities=ParentCompany;"  # Axis=Member; pairs
    assert_refused(select([fact(segments=segments)]), EpsStatus.DIMENSIONAL_ONLY)
    assert_refused(select([fact(coreg="ExampleOperatingPartnershipLP")]), EpsStatus.COREG_ONLY)
    assert select([fact(), fact(segments=segments)]).value == Decimal("1.5300")


# ── NULL ─────────────────────────────────────────────────────────────────────────────────


def test_a_matching_fact_with_a_null_value_is_null_value_not_zero_missing_or_ambiguous() -> None:
    chosen = fact(value=None)
    result = select([chosen])
    assert result.status is EpsStatus.NULL_VALUE
    assert result.value is None and result.fact_id == chosen.fact_id
    assert result.value != Decimal(0)


def test_a_null_and_a_valued_candidate_stay_ambiguous_rather_than_dropping_the_null() -> None:
    null_one = fact(taxonomy_version="us-gaap/2023", value=None)
    valued = fact(taxonomy_version="us-gaap/2024", value=Decimal("1.5300"))
    for ordering in itertools.permutations([null_one, valued]):
        assert_refused(select(list(ordering)), EpsStatus.AMBIGUOUS)


# ── dimensions and co-registrants ────────────────────────────────────────────────────────


def test_dimensional_only_eps_is_refused_and_no_class_is_chosen() -> None:
    class_a = fact(segments="StatementClassOfStockAxis=CommonClassAMember;", value=Decimal("1.1"))
    class_b = fact(segments="StatementClassOfStockAxis=CommonClassBMember;", value=Decimal("2.2"))
    results = [select(list(o)) for o in itertools.permutations([class_a, class_b])]
    for result in results:
        assert_refused(result, EpsStatus.DIMENSIONAL_ONLY)
    assert len({r.reason for r in results}) == 1


def test_a_non_dimensional_fact_is_selected_over_its_dimensional_variants() -> None:
    generic = fact(value=Decimal("1.5300"))
    class_a = fact(segments="StatementClassOfStockAxis=CommonClassAMember;", value=Decimal("9.1"))
    class_b = fact(segments="StatementClassOfStockAxis=CommonClassBMember;", value=Decimal("9.2"))
    coreg = fact(coreg="GuarantorSubsidiaryMember", value=Decimal("9.3"))
    for ordering in itertools.permutations([generic, class_a, class_b, coreg]):
        result = select(list(ordering))
        assert result.status is EpsStatus.AVAILABLE
        assert result.value == Decimal("1.5300") and (result.segments, result.coreg) == ("", "")


def test_coreg_only_eps_is_refused_and_not_treated_as_the_filing_companys() -> None:
    assert_refused(select([fact(coreg="GuarantorSubsidiaryMember")]), EpsStatus.COREG_ONLY)


def test_a_mix_of_coreg_and_dimensional_facts_without_a_plain_one_is_dimensional_only() -> None:
    mixed = [fact(coreg="GuarantorSubsidiaryMember"), fact(segments="ClassAxis=AMember;")]
    assert_refused(select(mixed), EpsStatus.DIMENSIONAL_ONLY)


def test_a_fact_with_both_segments_and_coreg_is_not_eligible() -> None:
    assert_refused(
        select([fact(segments="ClassAxis=AMember;", coreg="GuarantorSubsidiaryMember")]),
        EpsStatus.COREG_ONLY,
    )


def test_two_non_dimensional_candidates_are_ambiguous_in_every_order() -> None:
    one = fact(taxonomy_version="us-gaap/2022", value=Decimal("1.5300"))
    two = fact(taxonomy_version="us-gaap/2024", value=Decimal("1.5400"))
    three = fact(taxonomy_version="us-gaap/2021", value=Decimal("1.5500"))
    outcomes = {
        (r.status, r.reason, r.value, r.fact_id)
        for r in (select(list(o)) for o in itertools.permutations([one, two, three]))
    }
    assert len(outcomes) == 1
    assert next(iter(outcomes))[0] is EpsStatus.AMBIGUOUS


def test_a_result_never_depends_on_fact_order() -> None:
    pool = [
        fact(value=Decimal("1.5300")),
        fact(duration_quarters=2, value=Decimal("3.9100")),
        fact(period_end=PRIOR_YEAR_END, value=Decimal("1.5200")),
        fact(segments="ClassAxis=AMember;", value=Decimal("9.0000")),
        fact(coreg="Guarantor", value=Decimal("8.0000")),
        fact(unit="CAD", value=Decimal("153.0000")),
        fact(tag=BASIC, value=Decimal("1.6000")),
        fact(taxonomy_version="0000320193-24-000010", value=Decimal("7.0000")),
    ]
    baseline = select(pool)
    assert baseline.status is EpsStatus.AVAILABLE and baseline.value == Decimal("1.5300")
    for ordering in itertools.islice(itertools.permutations(pool), 0, 2000, 37):
        assert select(list(ordering)) == baseline


# ── amendments and the accession boundary ────────────────────────────────────────────────


def test_amendments_are_independent_accessions_with_no_supersession() -> None:
    original = fact(accession_number=ACC, value=Decimal("1.5300"))
    amendment = fact(accession_number=ACC_AMENDMENT, value=Decimal("1.5400"))
    on_original = select([original], accession=ACC)
    on_amendment = select([amendment], accession=ACC_AMENDMENT, form=SecFormType.FORM_10Q_A)
    assert on_original.value == Decimal("1.5300") and on_original.accession_number == ACC
    assert on_amendment.value == Decimal("1.5400")
    assert on_amendment.accession_number == ACC_AMENDMENT
    assert on_amendment.form is SecFormType.FORM_10Q_A


def test_a_fact_from_another_accession_is_refused_loudly_never_mixed_in() -> None:
    foreign = fact(accession_number=ACC_AMENDMENT)
    with pytest.raises(ValueError, match="never mixes accessions"):
        select([fact(), foreign], accession=ACC)


# ── the observation's own contract ───────────────────────────────────────────────────────


def _kwargs(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "accession_number": ACC,
        "company_cik": None,
        "form": SecFormType.FORM_10Q,
        "period_class": EpsPeriodClass.QUARTER,
        "eps_kind": EpsKind.DILUTED,
        "status": EpsStatus.AVAILABLE,
        "reason": "r",
        "fact_id": uuid.uuid4(),
        "tag": DILUTED,
        "taxonomy_version": GAAP,
        "unit": EPS_UNIT,
        "period_end": PERIOD_END,
        "duration_quarters": 1,
        "value": Decimal("1.5300"),
        "segments": "",
        "coreg": "",
    }
    base.update(overrides)
    return base


def test_an_observation_rejects_inconsistent_construction() -> None:
    with pytest.raises(ValueError, match="exact finite Decimal"):
        EpsFactObservation(**_kwargs(value=None))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="exact finite Decimal"):
        EpsFactObservation(**_kwargs(value=1.53))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="exact finite Decimal"):
        EpsFactObservation(**_kwargs(value=Decimal("NaN")))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="requires every fact field"):
        EpsFactObservation(**_kwargs(fact_id=None))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="must not carry a fact"):
        EpsFactObservation(**_kwargs(status=EpsStatus.MISSING, value=None))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="must state a reason"):
        EpsFactObservation(**_kwargs(reason=""))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="must carry no value"):
        EpsFactObservation(**_kwargs(status=EpsStatus.NULL_VALUE))  # type: ignore[arg-type]


def test_an_observation_rejects_a_malformed_accession_and_a_value_on_a_refusal() -> None:
    with pytest.raises(ValueError, match="accession_number must look like"):
        EpsFactObservation(**_kwargs(accession_number="not-an-accession"))  # type: ignore[arg-type]
    refusal_fields = {
        "fact_id": None,
        "tag": None,
        "taxonomy_version": None,
        "unit": None,
        "period_end": None,
        "duration_quarters": None,
        "segments": None,
        "coreg": None,
    }
    with pytest.raises(ValueError, match="must not carry a value"):
        EpsFactObservation(
            **_kwargs(status=EpsStatus.MISSING, value=Decimal("1.53"), **refusal_fields)  # type: ignore[arg-type]
        )


def test_the_package_exports_resolve_lazily_and_unknown_names_fail() -> None:
    assert financials.EpsStatus is EpsStatus
    assert financials.EPS_UNIT == EPS_UNIT
    assert financials.select_eps_fact is select_eps_fact
    assert "extract_eps_observation" in dir(financials)
    with pytest.raises(AttributeError, match="no attribute"):
        _ = financials.not_a_thing


def test_the_observation_is_immutable() -> None:
    result = select([fact()])
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.value = Decimal(0)  # type: ignore[misc]


def test_every_status_is_reachable_by_the_selector() -> None:
    seen = {
        select([fact()]).status,
        select([], period_ends=()).status,
        select([fact()], form=SecFormType.FORM_8K).status,
        select([]).status,
        select([fact(taxonomy_version="0000320193-24-000010")]).status,
        select([fact(unit="CAD")]).status,
        select([fact(segments="A=B;")]).status,
        select([fact(coreg="C")]).status,
        select([fact(value=None)]).status,
        select([fact(), fact(taxonomy_version="us-gaap/2023")]).status,
    }
    assert seen == set(EpsStatus)


# ── structural guarantees ────────────────────────────────────────────────────────────────

EPS_MODULES = ("financials/eps.py", "financials/eps_extraction.py", "financials/__init__.py")
FORBIDDEN_IMPORTS = (
    "quantforge.signals",
    "quantforge.research",
    "quantforge.backtest",
    "quantforge.market",
    "quantforge.identity",
    "quantforge.ingestion",
    "quantforge.providers",
    "quantforge.models.market",
    "quantforge.models.benchmark",
    "quantforge.models.earnings",
    "quantforge.models.corporate_action",
)
FORBIDDEN_FIELD_NAMES = {
    "available_at",
    "known_as_of",
    "observed_at",
    "accepted_at",
    "ingested_at",
    "pit_eligible",
    "source_accepted",
    "source_stated_accepted",
    "signal",
    "direction",
    "score",
    "growth",
    "surprise",
}


def _imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


@pytest.mark.parametrize("relative", EPS_MODULES)
def test_eps_modules_import_no_signal_return_execution_split_price_or_ingestion_code(
    relative: str,
) -> None:
    offenders = {
        name
        for name in _imports(SRC / relative)
        if any(name == bad or name.startswith(f"{bad}.") for bad in FORBIDDEN_IMPORTS)
    }
    assert offenders == set()


def test_the_pure_selector_does_not_even_import_the_repository_or_the_orm_session() -> None:
    names = _imports(SRC / "financials/eps.py")
    assert not any(n.startswith(("sqlalchemy", "quantforge.repositories")) for n in names)


@pytest.mark.parametrize("relative", EPS_MODULES)
def test_no_eps_module_uses_float_or_reads_an_availability_or_acceptance_attribute(
    relative: str,
) -> None:
    tree = ast.parse((SRC / relative).read_text(encoding="utf-8"))
    floats = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "float"
    ]
    assert floats == []
    attributes = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert attributes.isdisjoint(FORBIDDEN_FIELD_NAMES)


def test_the_result_and_input_types_carry_no_availability_or_signal_field() -> None:
    for model in (EpsFactObservation, EpsInputFact):
        assert {f.name for f in dataclasses.fields(model)}.isdisjoint(FORBIDDEN_FIELD_NAMES)


ALLOWED_FINANCIALS_CONSUMERS = {
    "api/v1/filing_eps.py",
    "api/v1/filing_eps_presenter.py",
    "schemas/filing_eps.py",
}  # the read-only filing EPS API (10.6J): no signal, backtest, research or UI consumer


def test_no_other_package_imports_the_financials_layer() -> None:
    offenders = []
    for path in SRC.rglob("*.py"):
        if "financials" in path.parts:
            continue
        if any(n.startswith("quantforge.financials") for n in _imports(path)):
            offenders.append(str(path.relative_to(SRC)))
    assert set(offenders) <= ALLOWED_FINANCIALS_CONSUMERS, (
        "only the read-only filing EPS API may consume it"
    )


# ── final-audit cases: period-end A-E and status precedence ──────────────────────────────


def test_period_end_cases_a_to_e_are_classified_as_documented() -> None:
    current = fact(value=Decimal("1.5300"))
    prior = fact(period_end=PRIOR_YEAR_END, value=Decimal("1.5200"))
    # A: exists at the filing's period -> normal selection
    assert select([current]).value == Decimal("1.5300")
    # B: only at the prior comparative period -> never current EPS
    only_prior = select([prior])
    assert_refused(only_prior, EpsStatus.MISSING)
    # C: both periods -> the current-period candidate only
    assert select([prior, current]).value == Decimal("1.5300")
    # D: several current-period candidates -> ambiguous
    twin = fact(taxonomy_version="us-gaap/2023", value=Decimal("1.5300"))
    assert_refused(select([current, twin, prior]), EpsStatus.AMBIGUOUS)
    # E: the tag exists, nothing at the current period -> MISSING, scoped to the current period
    assert "requested current period" in only_prior.reason
    assert "1 us-gaap EarningsPerShareDiluted fact(s) exist at other periods" in only_prior.reason


def test_status_precedence_over_unit_null_and_dimensions() -> None:
    class_a = fact(segments="ClassAxis=AMember;", value=Decimal("9.1"))
    # generic NULL + dimensional numeric -> the generic fact is the candidate: NULL_VALUE
    assert select([fact(value=None), class_a]).status is EpsStatus.NULL_VALUE
    # generic correct-unit + dimensional variants (any unit) -> generic selected
    wrong_unit_variant = fact(segments="ClassAxis=BMember;", unit="CAD")
    assert select([fact(), class_a, wrong_unit_variant]).value == Decimal("1.5300")
    # coreg-only correct unit -> COREG_ONLY; coreg-only wrong unit is still COREG_ONLY
    assert select([fact(coreg="G")]).status is EpsStatus.COREG_ONLY
    assert select([fact(coreg="G", unit="CAD")]).status is EpsStatus.COREG_ONLY
    # generic wrong-unit + dimensional correct-unit -> WRONG_UNIT
    assert select([fact(unit="CAD"), class_a]).status is EpsStatus.WRONG_UNIT
