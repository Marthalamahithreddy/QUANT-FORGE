"""Same-accession comparative EPS: the pure selector, with no database.

The comparative is "comparative EPS as presented in this filing". Candidates are always STORED
facts; the 12-month-end relationship only validates them. Covers current-failure propagation, the
12-month relationship (including the real 52/53-week and fiscal-year-change refusals), multi-column
annuals, YTD contamination, dimensions/coreg, unit, NULL, ambiguity, taxonomy, EPS kinds,
amendments, order independence, exact Decimals and the structural no-date-generation / no-PIT /
no-growth guarantees.
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
from quantforge.financials.comparative_eps import (
    COMPARATIVE_MONTH_DISTANCE,
    ComparativeEpsObservation,
    ComparativeEpsStatus,
    month_distance,
    select_comparative_eps,
)
from quantforge.financials.eps import (
    EpsFactObservation,
    EpsInputFact,
    EpsKind,
    EpsPeriodClass,
    EpsStatus,
    is_generic_fact,
)
from quantforge.models.enums import SecFormType

ACC = "0000320193-25-000010"
ACC_AMENDMENT = "0000320193-25-000020"
CURRENT_END = date(2025, 6, 30)
PRIOR_END = date(2024, 6, 30)
DILUTED = "EarningsPerShareDiluted"
BASIC_AND_DILUTED = "EarningsPerShareBasicAndDiluted"
GAAP = "us-gaap/2025"
SRC = Path(quantforge.__file__).resolve().parent
S = ComparativeEpsStatus


def month_end(year: int, month: int) -> date:
    # test-side helper only: builds fixture dates, never used by the production selector
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    return date.fromordinal(nxt.toordinal() - 1)


def months_before(anchor: date, months: int) -> date:
    index = anchor.year * 12 + (anchor.month - 1) - months
    return month_end(index // 12, index % 12 + 1)


def fact(**overrides: object) -> EpsInputFact:
    fields: dict[str, object] = {
        "fact_id": uuid.uuid4(),
        "accession_number": ACC,
        "tag": DILUTED,
        "taxonomy_version": GAAP,
        "unit": "USD",
        "period_end": CURRENT_END,
        "duration_quarters": 1,
        "value": Decimal("1.5300"),
        "segments": "",
        "coreg": "",
    }
    fields.update(overrides)
    return EpsInputFact(**fields)  # type: ignore[arg-type]


def current_fact(**overrides: object) -> EpsInputFact:
    return fact(**overrides)


def prior_fact(**overrides: object) -> EpsInputFact:
    fields: dict[str, object] = {"period_end": PRIOR_END, "value": Decimal("1.3100")}
    fields.update(overrides)
    return fact(**fields)


def select(
    facts: list[EpsInputFact],
    *,
    form: SecFormType = SecFormType.FORM_10Q,
    period: EpsPeriodClass = EpsPeriodClass.QUARTER,
    kind: EpsKind = EpsKind.DILUTED,
    period_ends: tuple[date, ...] = (CURRENT_END,),
    accession: str = ACC,
) -> ComparativeEpsObservation:
    return select_comparative_eps(
        accession_number=accession,
        company_cik="0000320193",
        form=form,
        submission_period_ends=period_ends,
        facts=facts,
        period_class=period,
        eps_kind=kind,
    )


def assert_refused(result: ComparativeEpsObservation, status: ComparativeEpsStatus) -> None:
    assert result.status is status
    assert result.comparative_fact is None
    assert result.comparative_value is None
    assert result.reason


# ── the core cases ───────────────────────────────────────────────────────────────────────


def test_a_simple_10q_comparative_is_available_with_exact_decimals_and_no_arithmetic() -> None:
    cur, pri = current_fact(), prior_fact()
    result = select([cur, pri])
    assert result.status is S.AVAILABLE
    assert (
        result.comparative_value == Decimal("1.3100") and str(result.comparative_value) == "1.3100"
    )
    assert isinstance(result.comparative_value, Decimal)
    assert (
        result.current.value == Decimal("1.5300") and result.current.status is EpsStatus.AVAILABLE
    )
    assert result.comparative_fact_id == pri.fact_id
    assert result.comparative_period_end == PRIOR_END
    assert result.period_month_distance == COMPARATIVE_MONTH_DISTANCE == 12
    assert "comparative EPS as presented in this filing" in result.reason
    assert result.period_class is EpsPeriodClass.QUARTER and result.eps_kind is EpsKind.DILUTED


@pytest.mark.parametrize("ytd", [2, 3])
def test_a_quarter_request_never_mixes_in_a_year_to_date_comparative(ytd: int) -> None:
    pool = [
        current_fact(value=Decimal("1.5300")),
        current_fact(duration_quarters=ytd, value=Decimal("3.0000")),
        prior_fact(),
        prior_fact(duration_quarters=ytd, value=Decimal("2.6000")),
    ]
    for ordering in itertools.permutations(pool):
        result = select(list(ordering))
        assert result.status is S.AVAILABLE and result.comparative_value == Decimal("1.3100")
        assert (
            result.comparative_fact is not None and result.comparative_fact.duration_quarters == 1
        )


@pytest.mark.parametrize("ytd", [2, 3])
def test_a_prior_year_to_date_only_is_a_missing_comparative_never_a_quarter(ytd: int) -> None:
    result = select([current_fact(), prior_fact(duration_quarters=ytd, value=Decimal("2.6"))])
    assert_refused(result, S.COMPARATIVE_MISSING)


def test_an_annual_10k_with_several_earlier_columns_selects_the_one_at_the_relationship() -> None:
    current = date(2025, 12, 31)
    cols = [
        fact(period_end=current, duration_quarters=4, value=Decimal("6.0000")),
        fact(period_end=date(2024, 12, 31), duration_quarters=4, value=Decimal("5.0000")),
        fact(period_end=date(2023, 12, 31), duration_quarters=4, value=Decimal("4.0000")),
    ]
    outcomes = set()
    for ordering in itertools.permutations(cols):
        result = select(
            list(ordering),
            form=SecFormType.FORM_10K,
            period=EpsPeriodClass.ANNUAL,
            period_ends=(current,),
        )
        outcomes.add(
            (result.status, result.comparative_value, result.comparative_period_end, result.reason)
        )
    assert outcomes == {
        (S.AVAILABLE, Decimal("5.0000"), date(2024, 12, 31), next(iter(outcomes))[3])
    }, "2023 does not make the result ambiguous"


def test_leap_day_month_ends_still_validate_by_month_position_not_days() -> None:
    cur_end = date(2025, 2, 28)
    prior = fact(period_end=date(2024, 2, 29), value=Decimal("1.0000"))  # 365 days earlier
    result = select([fact(period_end=cur_end), prior], period_ends=(cur_end,))
    assert result.status is S.AVAILABLE and result.period_month_distance == 12
    leap_current = date(2024, 2, 29)
    result = select(
        [fact(period_end=leap_current), fact(period_end=date(2023, 2, 28))],
        period_ends=(leap_current,),
    )
    assert result.status is S.AVAILABLE, "366 days earlier: still 12 month-ends"


# ── refusals of the period relationship ──────────────────────────────────────────────────


@pytest.mark.parametrize("months", [1, 6, 9, 11, 13, 16, 18, 20, 24])
def test_an_earlier_candidate_not_exactly_12_month_ends_before_is_a_period_mismatch(
    months: int,
) -> None:
    earlier = fact(period_end=months_before(CURRENT_END, months), value=Decimal("1.1"))
    result = select([current_fact(), earlier])
    assert_refused(result, S.COMPARATIVE_PERIOD_MISMATCH)
    assert f"{months} months before" in result.reason


def test_the_real_52_53_week_pattern_is_refused_without_special_casing_the_issuer() -> None:
    # A real retailer: current period rounds to Jan 31, the prior-year comparative to Feb 28.
    current = date(2026, 1, 31)
    result = select(
        [fact(period_end=current), fact(period_end=date(2025, 2, 28))], period_ends=(current,)
    )
    assert_refused(result, S.COMPARATIVE_PERIOD_MISMATCH)
    assert result.current.status is EpsStatus.AVAILABLE
    assert "52/53-week" in result.reason


def test_a_fiscal_year_change_with_a_16_month_candidate_is_refused() -> None:
    current = date(2025, 12, 31)
    result = select(
        [
            fact(period_end=current, duration_quarters=4),
            fact(period_end=date(2024, 8, 31), duration_quarters=4),
        ],
        form=SecFormType.FORM_10K,
        period=EpsPeriodClass.ANNUAL,
        period_ends=(current,),
    )
    assert_refused(result, S.COMPARATIVE_PERIOD_MISMATCH)


def test_a_non_month_end_date_cannot_be_validated_and_is_refused() -> None:
    # FSDS dates are month-end rounded; an unrounded date is not evidence of the relationship.
    result = select([current_fact(), fact(period_end=date(2024, 6, 29))])
    assert_refused(result, S.COMPARATIVE_PERIOD_MISMATCH)
    unrounded_current = date(2025, 6, 29)
    result = select(
        [fact(period_end=unrounded_current), prior_fact()], period_ends=(unrounded_current,)
    )
    assert_refused(result, S.COMPARATIVE_PERIOD_MISMATCH)


def test_a_fact_at_the_current_period_end_is_not_an_earlier_candidate() -> None:
    twin = fact(taxonomy_version="us-gaap/2023", value=Decimal("1.0"))  # same period end as current
    result = select([current_fact(taxonomy_version="us-gaap/2024"), twin])
    assert result.status is S.CURRENT_UNAVAILABLE and result.current.status is EpsStatus.AMBIGUOUS


def test_the_month_distance_helper_is_pure_year_month_arithmetic() -> None:
    assert month_distance(date(2025, 6, 30), date(2024, 6, 30)) == 12
    assert month_distance(date(2025, 2, 28), date(2024, 2, 29)) == 12
    assert month_distance(date(2026, 1, 31), date(2025, 2, 28)) == 11
    assert month_distance(date(2025, 6, 30), date(2025, 6, 30)) == 0


def test_later_facts_and_the_current_fact_itself_are_never_comparative_candidates() -> None:
    later = fact(period_end=date(2026, 6, 30), value=Decimal("9.9"))
    assert_refused(select([current_fact(), later]), S.COMPARATIVE_MISSING)
    assert_refused(select([current_fact()]), S.COMPARATIVE_MISSING)


# ── missing, and what is never a candidate ───────────────────────────────────────────────


def test_no_earlier_same_concept_evidence_is_a_missing_comparative() -> None:
    assert_refused(select([current_fact()]), S.COMPARATIVE_MISSING)


@pytest.mark.parametrize(
    "stranger",
    [
        {"tag": "EarningsPerShareBasic"},
        {"tag": "IncomeLossFromContinuingOperationsPerDilutedShare"},
        {"tag": "AdjustedDilutedEps", "taxonomy_version": "0000320193-25-000010"},
        {"taxonomy_version": "0000320193-25-000010"},  # issuer extension with the standard name
        {"taxonomy_version": "ifrs-full/2024"},
        {"taxonomy_version": "us-gaap/24"},
        {"duration_quarters": 4},
    ],
)
def test_other_concepts_extensions_malformed_versions_and_durations_are_not_candidates(
    stranger: dict[str, object],
) -> None:
    assert_refused(select([current_fact(), prior_fact(**stranger)]), S.COMPARATIVE_MISSING)


@pytest.mark.parametrize("prior_version", ["us-gaap/2009", "us-gaap/2018", "us-gaap/2024", GAAP])
def test_the_comparative_may_use_any_standard_us_gaap_release(prior_version: str) -> None:
    result = select([current_fact(), prior_fact(taxonomy_version=prior_version)])
    assert result.status is S.AVAILABLE
    assert result.comparative_fact is not None
    assert result.comparative_fact.taxonomy_version == prior_version


def test_two_standard_releases_at_the_validated_period_are_ambiguous_even_with_equal_values() -> (
    None
):
    a = prior_fact(taxonomy_version="us-gaap/2023", value=Decimal("1.3100"))
    b = prior_fact(taxonomy_version="us-gaap/2024", value=Decimal("1.3100"))
    results = [select([current_fact(), *o]) for o in itertools.permutations([a, b])]
    for result in results:
        assert_refused(result, S.COMPARATIVE_AMBIGUOUS)
    assert len({r.reason for r in results}) == 1
    assert "us-gaap/2023, us-gaap/2024" in results[0].reason


# ── dimensions, co-registrants, unit, NULL ───────────────────────────────────────────────


def test_dimensional_only_comparative_is_refused_and_no_class_is_chosen() -> None:
    a = prior_fact(segments="ClassOfStock=CommonClassA;")
    b = prior_fact(segments="ClassOfStock=CommonClassB;ConsolidatedEntities=ParentCompany;")
    for ordering in itertools.permutations([a, b]):
        assert_refused(select([current_fact(), *ordering]), S.COMPARATIVE_DIMENSIONAL_ONLY)


def test_a_generic_comparative_wins_over_dimensional_and_coreg_variants() -> None:
    generic = prior_fact(value=Decimal("1.3100"))
    variants = [
        prior_fact(segments="ClassOfStock=CommonClassA;", value=Decimal("9.1")),
        prior_fact(coreg="ExampleOperatingPartnershipLP", value=Decimal("9.2")),
        prior_fact(unit="CAD", segments="X=Y;", value=Decimal("9.3")),
    ]
    for ordering in itertools.permutations([generic, *variants]):
        result = select([current_fact(), *ordering])
        assert result.status is S.AVAILABLE and result.comparative_value == Decimal("1.3100")


def test_a_coreg_only_comparative_is_refused_and_not_treated_as_the_companys_eps() -> None:
    assert_refused(
        select([current_fact(), prior_fact(coreg="ExampleOperatingPartnershipLP")]),
        S.COMPARATIVE_COREG_ONLY,
    )


def test_coreg_and_dimensional_mix_without_a_generic_fact_is_dimensional_only() -> None:
    mixed = [prior_fact(coreg="G"), prior_fact(segments="ClassAxis=A;")]
    assert_refused(select([current_fact(), *mixed]), S.COMPARATIVE_DIMENSIONAL_ONLY)


def test_a_wrong_unit_comparative_is_not_reported_as_missing_and_is_never_converted() -> None:
    for unit in ("CAD", "EUR", "USD/shares", "shares", "usd"):
        result = select([current_fact(), prior_fact(unit=unit)])
        assert_refused(result, S.COMPARATIVE_WRONG_UNIT)
        assert unit in result.reason


def test_the_current_units_comparative_wins_over_a_co_reported_other_currency() -> None:
    usd = prior_fact(value=Decimal("1.3100"))
    cad = prior_fact(unit="CAD", value=Decimal("1.7900"))
    for ordering in itertools.permutations([usd, cad]):
        assert select([current_fact(), *ordering]).comparative_value == Decimal("1.3100")


def test_a_wrong_unit_generic_fact_is_not_hidden_by_right_unit_variants() -> None:
    pool = [
        prior_fact(unit="CAD"),
        prior_fact(segments="ClassOfStock=CommonClassA;"),
        prior_fact(coreg="G"),
    ]
    for ordering in itertools.permutations(pool):
        assert_refused(select([current_fact(), *ordering]), S.COMPARATIVE_WRONG_UNIT)


def test_a_single_null_comparative_is_null_value_with_its_evidence_not_zero_or_missing() -> None:
    chosen = prior_fact(value=None)
    result = select([current_fact(), chosen])
    assert result.status is S.COMPARATIVE_NULL_VALUE
    assert result.comparative_fact is chosen and result.comparative_fact_id == chosen.fact_id
    assert result.comparative_value is None and result.comparative_period_end == PRIOR_END


def test_a_numeric_and_a_null_candidate_are_ambiguous_not_resolved_in_favour_of_the_number() -> (
    None
):
    null_one = prior_fact(taxonomy_version="us-gaap/2023", value=None)
    valued = prior_fact(taxonomy_version="us-gaap/2024", value=Decimal("1.3100"))
    for ordering in itertools.permutations([null_one, valued]):
        assert_refused(select([current_fact(), *ordering]), S.COMPARATIVE_AMBIGUOUS)


def test_ambiguity_has_no_tie_break_by_value_version_or_order() -> None:
    pool = [
        prior_fact(taxonomy_version="us-gaap/2022", value=Decimal("1.0")),
        prior_fact(taxonomy_version="us-gaap/2024", value=Decimal("2.0")),
        prior_fact(taxonomy_version="us-gaap/2021", value=Decimal("3.0")),
    ]
    outcomes = {
        (r.status, r.reason, r.comparative_fact_id)
        for r in (select([current_fact(), *o]) for o in itertools.permutations(pool))
    }
    assert len(outcomes) == 1 and next(iter(outcomes))[0] is S.COMPARATIVE_AMBIGUOUS


# ── EPS kinds ────────────────────────────────────────────────────────────────────────────


def test_basic_and_diluted_uses_only_its_own_concept_for_current_and_comparative() -> None:
    both_cur = current_fact(tag=BASIC_AND_DILUTED, value=Decimal("2.0000"))
    both_pri = prior_fact(tag=BASIC_AND_DILUTED, value=Decimal("1.9000"))
    result = select([both_cur, both_pri], kind=EpsKind.BASIC_AND_DILUTED)
    assert result.status is S.AVAILABLE and result.comparative_value == Decimal("1.9000")
    assert result.eps_kind is EpsKind.BASIC_AND_DILUTED
    assert result.comparative_fact is not None and result.comparative_fact.tag == BASIC_AND_DILUTED


def test_there_is_no_cross_kind_fallback_for_the_comparative() -> None:
    # DILUTED current, comparative only exists as BasicAndDiluted: not substituted.
    result = select([current_fact(), prior_fact(tag=BASIC_AND_DILUTED)])
    assert_refused(result, S.COMPARATIVE_MISSING)
    # BasicAndDiluted current, comparative only exists as Diluted: not substituted.
    result = select(
        [current_fact(tag=BASIC_AND_DILUTED), prior_fact(tag=DILUTED)],
        kind=EpsKind.BASIC_AND_DILUTED,
    )
    assert_refused(result, S.COMPARATIVE_MISSING)


# ── current failure propagation ──────────────────────────────────────────────────────────


def test_every_current_failure_is_current_unavailable_and_no_historical_search_happens() -> None:
    perfect_prior = prior_fact()
    cases = {
        EpsStatus.MISSING: [perfect_prior],
        EpsStatus.WRONG_UNIT: [current_fact(unit="CAD"), perfect_prior],
        EpsStatus.NULL_VALUE: [current_fact(value=None), perfect_prior],
        EpsStatus.AMBIGUOUS: [
            current_fact(taxonomy_version="us-gaap/2024"),
            current_fact(taxonomy_version="us-gaap/2023"),
            perfect_prior,
        ],
        EpsStatus.DIMENSIONAL_ONLY: [current_fact(segments="ClassAxis=A;"), perfect_prior],
        EpsStatus.COREG_ONLY: [current_fact(coreg="G"), perfect_prior],
        EpsStatus.EXTENSION_ONLY: [
            current_fact(taxonomy_version="0000320193-25-000010"),
            perfect_prior,
        ],
    }
    for current_status, facts in cases.items():
        result = select(facts)
        assert result.status is S.CURRENT_UNAVAILABLE, current_status
        assert result.current.status is current_status
        assert result.comparative_fact is None
        assert current_status.value in result.reason


def test_not_ingested_and_unsupported_form_period_also_propagate() -> None:
    not_ingested = select([prior_fact()], period_ends=())
    assert not_ingested.status is S.CURRENT_UNAVAILABLE
    assert not_ingested.current.status is EpsStatus.NOT_INGESTED
    unsupported = select([current_fact(), prior_fact()], form=SecFormType.FORM_10K)
    assert unsupported.status is S.CURRENT_UNAVAILABLE
    assert unsupported.current.status is EpsStatus.UNSUPPORTED_FORM_PERIOD


# ── accession scope and amendments ───────────────────────────────────────────────────────


def test_amendments_resolve_independently_with_no_supersession() -> None:
    original = [
        fact(accession_number=ACC, value=Decimal("1.5300")),
        fact(accession_number=ACC, period_end=PRIOR_END, value=Decimal("1.3100")),
    ]
    amendment = [
        fact(accession_number=ACC_AMENDMENT, value=Decimal("1.5400")),
        fact(accession_number=ACC_AMENDMENT, period_end=PRIOR_END, value=Decimal("1.2900")),
    ]
    first = select(original, accession=ACC)
    second = select(amendment, accession=ACC_AMENDMENT, form=SecFormType.FORM_10Q_A)
    assert first.comparative_value == Decimal("1.3100") and first.current.value == Decimal("1.5300")
    assert second.comparative_value == Decimal("1.2900") and second.current.value == Decimal(
        "1.5400"
    )
    assert second.form is SecFormType.FORM_10Q_A


def test_a_fact_from_another_accession_is_refused_loudly_never_used_as_a_comparative() -> None:
    foreign = fact(accession_number=ACC_AMENDMENT, period_end=PRIOR_END)
    with pytest.raises(ValueError, match="never mixes accessions"):
        select([current_fact(), foreign])


# ── order independence and the observation's contract ────────────────────────────────────


def test_a_result_never_depends_on_fact_order() -> None:
    pool = [
        current_fact(),
        current_fact(duration_quarters=2, value=Decimal("3.0")),
        prior_fact(),
        prior_fact(duration_quarters=2, value=Decimal("2.6")),
        fact(period_end=date(2023, 6, 30), value=Decimal("1.1")),
        prior_fact(segments="ClassAxis=A;", value=Decimal("9.0")),
        prior_fact(coreg="G", value=Decimal("8.0")),
        prior_fact(unit="CAD", value=Decimal("7.0")),
        prior_fact(tag="EarningsPerShareBasic", value=Decimal("1.4")),
        prior_fact(taxonomy_version="0000320193-25-000010", value=Decimal("6.0")),
    ]
    baseline = select(pool)
    assert baseline.status is S.AVAILABLE and baseline.comparative_value == Decimal("1.3100")
    for ordering in itertools.islice(itertools.permutations(pool), 0, 3000, 53):
        assert select(list(ordering)) == baseline


def _observation(**overrides: object) -> ComparativeEpsObservation:
    base = select([current_fact(), prior_fact()])
    fields: dict[str, object] = {
        f.name: getattr(base, f.name) for f in dataclasses.fields(ComparativeEpsObservation)
    }
    fields.update(overrides)
    return ComparativeEpsObservation(**fields)  # type: ignore[arg-type]


def test_the_observation_rejects_inconsistent_construction() -> None:
    base = select([current_fact(), prior_fact()])
    with pytest.raises(ValueError, match="requires a comparative fact"):
        _observation(comparative_fact=None)
    with pytest.raises(ValueError, match="must not carry a comparative fact"):
        _observation(status=S.COMPARATIVE_MISSING)
    with pytest.raises(ValueError, match="holds exactly when"):
        _observation(status=S.CURRENT_UNAVAILABLE, comparative_fact=None)
    with pytest.raises(ValueError, match="validated period relationship"):
        _observation(comparative_fact=fact(period_end=date(2024, 5, 31)))
    with pytest.raises(ValueError, match="validated period relationship"):
        _observation(comparative_fact=prior_fact(duration_quarters=2))
    with pytest.raises(ValueError, match="exact finite Decimal"):
        _observation(comparative_fact=prior_fact(value=None))
    with pytest.raises(ValueError, match="exact finite Decimal"):
        _observation(comparative_fact=prior_fact(value=Decimal("NaN")))
    with pytest.raises(ValueError, match="different accession"):
        _observation(comparative_fact=prior_fact(accession_number=ACC_AMENDMENT))
    with pytest.raises(ValueError, match="different accession"):
        _observation(accession_number=ACC_AMENDMENT)
    with pytest.raises(ValueError, match="must state a reason"):
        _observation(reason="")
    with pytest.raises(ValueError, match="must carry no value"):
        _observation(
            status=S.COMPARATIVE_NULL_VALUE, comparative_fact=prior_fact(value=Decimal("1"))
        )
    assert base.status is S.AVAILABLE


def test_the_derived_month_distance_is_absent_without_a_comparative_fact() -> None:
    assert select([current_fact()]).period_month_distance is None


def test_the_observation_is_immutable() -> None:
    result = select([current_fact(), prior_fact()])
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.status = S.COMPARATIVE_MISSING  # type: ignore[misc]


def test_every_comparative_status_is_reachable_by_the_selector() -> None:
    seen = {
        select([current_fact(), prior_fact()]).status,
        select([prior_fact()]).status,
        select([current_fact()]).status,
        select([current_fact(), fact(period_end=date(2025, 1, 31))]).status,
        select([current_fact(), prior_fact(segments="A=B;")]).status,
        select([current_fact(), prior_fact(coreg="C")]).status,
        select([current_fact(), prior_fact(unit="CAD")]).status,
        select([current_fact(), prior_fact(value=None)]).status,
        select([current_fact(), prior_fact(), prior_fact(taxonomy_version="us-gaap/2020")]).status,
    }
    assert seen == set(ComparativeEpsStatus)


def test_the_package_exports_resolve_lazily() -> None:
    assert financials.ComparativeEpsStatus is ComparativeEpsStatus
    assert financials.select_comparative_eps is select_comparative_eps
    assert "extract_comparative_eps_observation" in dir(financials)


# ── structural guarantees ────────────────────────────────────────────────────────────────

COMPARATIVE_MODULES = ("financials/comparative_eps.py", "financials/comparative_eps_extraction.py")
FORBIDDEN_IMPORTS = (
    "quantforge.signals",
    "quantforge.research",
    "quantforge.backtest",
    "quantforge.market",
    "quantforge.identity",
    "quantforge.ingestion.splits",
    "quantforge.providers",
    "quantforge.models.market",
    "quantforge.models.benchmark",
    "quantforge.models.earnings",
    "quantforge.models.corporate_action",
)
FORBIDDEN_NAMES = {
    "available_at",
    "known_as_of",
    "observed_at",
    "accepted_at",
    "ingested_at",
    "source_accepted",
    "source_stated_accepted",
    "pit_eligible",
    "signal",
    "direction",
    "score",
    "growth",
    "surprise",
    "pct_change",
    "yoy",
}
DATE_GENERATORS = {"timedelta", "relativedelta", "today", "utcnow", "now"}


def _imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


@pytest.mark.parametrize("relative", COMPARATIVE_MODULES)
def test_comparative_modules_import_no_signal_return_price_execution_split_or_provider_code(
    relative: str,
) -> None:
    offenders = {
        name
        for name in _imports(SRC / relative)
        if any(name == bad or name.startswith(f"{bad}.") for bad in FORBIDDEN_IMPORTS)
    }
    assert offenders == set()


@pytest.mark.parametrize("relative", COMPARATIVE_MODULES)
def test_no_comparative_module_uses_float_generates_a_date_or_reads_availability(
    relative: str,
) -> None:
    tree = ast.parse((SRC / relative).read_text(encoding="utf-8"))
    calls = {
        (n.func.id if isinstance(n.func, ast.Name) else n.func.attr)
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, (ast.Name, ast.Attribute))
    }
    assert "float" not in calls
    assert calls.isdisjoint(DATE_GENERATORS), "candidates are stored facts, never generated dates"
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} | {
        n.id for n in ast.walk(tree) if isinstance(n, ast.Name)
    }
    assert names.isdisjoint(FORBIDDEN_NAMES)
    imported = _imports(SRC / relative)
    assert not {"dateutil", "dateutil.relativedelta", "pandas"} & imported


def test_the_comparative_result_type_carries_no_growth_availability_or_signal_field() -> None:
    assert {f.name for f in dataclasses.fields(ComparativeEpsObservation)}.isdisjoint(
        FORBIDDEN_NAMES
    )
    assert isinstance(select([current_fact(), prior_fact()]).current, EpsFactObservation)


def test_the_comparative_layer_adds_no_sql_selection_and_reuses_the_factual_repository() -> None:
    adapter = (SRC / "financials/comparative_eps_extraction.py").read_text(encoding="utf-8")
    shared = (SRC / "financials/eps_extraction.py").read_text(encoding="utf-8")
    for text in (adapter, shared):
        assert "ORDER BY" not in text.upper().replace("ORDER_BY", "")
        assert "limit(" not in text.lower() and ".limit" not in text.lower()
    assert "facts_for_filing_tag" in shared


def test_an_unrounded_current_period_end_is_reported_as_an_untestable_relationship() -> None:
    unrounded = date(2025, 6, 29)
    result = select([fact(period_end=unrounded), prior_fact()], period_ends=(unrounded,))
    assert_refused(result, S.COMPARATIVE_PERIOD_MISMATCH)
    assert "cannot be validated against any earlier fact" in result.reason
    assert "52/53-week" not in result.reason, "the cause is the current date, not the earlier facts"


def test_ignored_same_name_extension_comparatives_are_reported_never_mapped() -> None:
    result = select([current_fact(), prior_fact(taxonomy_version="0000320193-25-000010")])
    assert_refused(result, S.COMPARATIVE_MISSING)
    assert "1 non-us-gaap-version fact(s) with the same name" in result.reason


def test_the_observation_enforces_generic_unit_taxonomy_and_envelope_agreement() -> None:
    for bad in (
        prior_fact(segments="ClassAxis=A;"),
        prior_fact(coreg="G"),
        prior_fact(unit="CAD"),
        prior_fact(taxonomy_version="0000320193-25-000010"),
    ):
        with pytest.raises(ValueError, match="generic, standard us-gaap and in the current unit"):
            _observation(comparative_fact=bad)
    for override in (
        {"period_class": EpsPeriodClass.ANNUAL},
        {"eps_kind": EpsKind.BASIC_AND_DILUTED},
        {"form": SecFormType.FORM_10Q_A},
        {"company_cik": "0000000001"},
    ):
        with pytest.raises(ValueError, match="does not match the requested"):
            _observation(**override)


def test_the_generic_rule_is_defined_once_and_shared_with_current_eps() -> None:
    assert is_generic_fact(fact()) and not is_generic_fact(fact(segments="A=B;"))
    assert not is_generic_fact(fact(coreg="G"))
    comparative = (SRC / "financials/comparative_eps.py").read_text(encoding="utf-8")
    assert 'segments == ""' not in comparative, "the rule lives in eps.py only"


# ── final audit: the status boundary, mixed evidence, month ends ─────────────────────────


@pytest.mark.parametrize(
    "stranger",
    [
        {"tag": "EarningsPerShareBasic"},
        {"tag": BASIC_AND_DILUTED},  # BasicAndDiluted while DILUTED is requested
        {"tag": "IncomeLossFromContinuingOperationsPerDilutedShare"},
        {"tag": "AdjustedDilutedEps", "taxonomy_version": "0000320193-25-000010"},
        {"taxonomy_version": "0000320193-25-000010"},
        {"taxonomy_version": "ifrs-full/2024"},
        {"duration_quarters": 2},
        {"duration_quarters": 4},
    ],
)
@pytest.mark.parametrize("months", [9, 11, 13, 24])
def test_unrelated_earlier_facts_never_cause_a_period_mismatch_even_off_the_relationship(
    stranger: dict[str, object], months: int
) -> None:
    unrelated = fact(period_end=months_before(CURRENT_END, months), **stranger)
    assert_refused(select([current_fact(), unrelated]), S.COMPARATIVE_MISSING)


@pytest.mark.parametrize("months", [9, 11, 13, 24])
def test_structurally_relevant_earlier_facts_off_the_relationship_are_a_period_mismatch(
    months: int,
) -> None:
    # same accession, exact tag, standard version, same qtrs, earlier: relevant, whatever the unit/dims
    for variant in ({}, {"unit": "CAD"}, {"segments": "ClassAxis=A;"}, {"coreg": "G"}):
        earlier = fact(period_end=months_before(CURRENT_END, months), **variant)
        assert_refused(select([current_fact(), earlier]), S.COMPARATIVE_PERIOD_MISMATCH)


def test_a_foreign_accession_fact_is_never_relevant_evidence() -> None:
    foreign = fact(accession_number=ACC_AMENDMENT, period_end=months_before(CURRENT_END, 11))
    with pytest.raises(ValueError, match="never mixes accessions"):
        select([current_fact(), foreign])


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        ([("generic", "CAD"), ("dim", "USD")], S.COMPARATIVE_WRONG_UNIT),
        ([("generic", "USD"), ("dim", "CAD")], S.AVAILABLE),
        ([("dim", "USD")], S.COMPARATIVE_DIMENSIONAL_ONLY),
        ([("dim", "CAD")], S.COMPARATIVE_DIMENSIONAL_ONLY),
        ([("coreg", "USD"), ("generic", "CAD")], S.COMPARATIVE_WRONG_UNIT),
        ([("generic", "USD"), ("coreg", "CAD")], S.AVAILABLE),
        ([("coreg", "USD")], S.COMPARATIVE_COREG_ONLY),
        ([("coreg", "CAD"), ("dim", "USD")], S.COMPARATIVE_DIMENSIONAL_ONLY),
    ],
)
def test_mixed_dimension_and_unit_evidence_follows_the_generic_policy_in_any_order(
    facts: list[tuple[str, str]], expected: ComparativeEpsStatus
) -> None:
    kinds = {
        "generic": {},
        "dim": {"segments": "ClassOfStock=CommonClassA;"},
        "coreg": {"coreg": "ExampleOperatingPartnershipLP"},
    }
    built = [prior_fact(unit=unit, **kinds[kind]) for kind, unit in facts]
    outcomes = {
        (r.status, r.reason)
        for r in (select([current_fact(), *o]) for o in itertools.permutations(built))
    }
    assert len(outcomes) == 1
    assert next(iter(outcomes))[0] is expected


@pytest.mark.parametrize(
    ("current", "prior", "expected"),
    [
        (date(2025, 2, 28), date(2024, 2, 29), S.AVAILABLE),  # leap February
        (date(2024, 2, 29), date(2023, 2, 28), S.AVAILABLE),
        (date(2025, 4, 30), date(2024, 4, 30), S.AVAILABLE),
        (date(2025, 12, 31), date(2024, 12, 31), S.AVAILABLE),
        (date(2025, 2, 28), date(2024, 2, 28), S.COMPARATIVE_PERIOD_MISMATCH),  # not a month end
        (date(2025, 4, 30), date(2024, 4, 29), S.COMPARATIVE_PERIOD_MISMATCH),
        (
            date(2025, 12, 30),
            date(2024, 12, 31),
            S.COMPARATIVE_PERIOD_MISMATCH,
        ),  # current unrounded
    ],
)
def test_the_month_end_requirement_and_no_identical_day_requirement(
    current: date, prior: date, expected: ComparativeEpsStatus
) -> None:
    result = select(
        [fact(period_end=current), fact(period_end=prior, value=Decimal("1.0"))],
        period_ends=(current,),
    )
    assert result.status is expected
