"""One company's stored filings, each with revenue, net income and diluted EPS beside the prior year.

A thin database adapter over the existing pure selectors. Revenue and net income come from
:mod:`quantforge.financials.period_metrics`; diluted EPS comes from the unchanged frozen path
(:func:`~quantforge.financials.comparative_eps_extraction.extract_comparative_eps_observation` and
:func:`~quantforge.financials.eps_change.compute_eps_change`), so there is one EPS implementation.

Reads stored rows only: no SEC or provider call, no write, no cross-filing adjustment. Every filing
is reported with its own figures; a later filing's restatement of an earlier period is a separate
row, and no "latest" is chosen. Quarterly (10-Q) and annual (10-K) filings are separate rows and
are never mixed: year-to-date amounts are not used.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from quantforge.financials.comparative_eps import ComparativeEpsObservation, ComparativeEpsStatus
from quantforge.financials.comparative_eps_extraction import extract_comparative_eps_observation
from quantforge.financials.eps import (
    EpsFactObservation,
    EpsInputFact,
    EpsKind,
    EpsPeriodClass,
)
from quantforge.financials.eps_change import EpsChangeObservation, compute_eps_change
from quantforge.financials.period_metrics import (
    MONEY_TAG,
    MetricChange,
    MetricPoint,
    MoneyMetric,
    PointStatus,
    compare_money_metric,
)
from quantforge.models.company import Company
from quantforge.models.enums import SecFormType
from quantforge.repositories.financial_fact import CompanyFilingFacts, FinancialFactRepository

__all__ = [
    "FILING_FORMS",
    "MAX_FILINGS",
    "CompanyHistory",
    "FilingFinancials",
    "MetricComparison",
    "build_company_financials",
]

#: A company's page reads this many of its newest filings (each costs several queries). The total is
#: always reported beside it so a shorter list is never mistaken for the whole history.
MAX_FILINGS = 40

#: The forms that have a defined period class. Anything else is not read.
_PERIOD_CLASS: dict[SecFormType, EpsPeriodClass] = {
    SecFormType.FORM_10Q: EpsPeriodClass.QUARTER,
    SecFormType.FORM_10Q_A: EpsPeriodClass.QUARTER,
    SecFormType.FORM_10K: EpsPeriodClass.ANNUAL,
    SecFormType.FORM_10K_A: EpsPeriodClass.ANNUAL,
}
FILING_FORMS: tuple[SecFormType, ...] = tuple(_PERIOD_CLASS)


@dataclass(frozen=True, slots=True)
class MetricComparison:
    """``metric`` is a :class:`MoneyMetric` value or ``EPS_DILUTED``."""

    metric: str
    current: MetricPoint
    prior: MetricPoint
    change: MetricChange


@dataclass(frozen=True, slots=True)
class FilingFinancials:
    filing: CompanyFilingFacts
    period_class: EpsPeriodClass
    comparisons: tuple[MetricComparison, ...]


@dataclass(frozen=True, slots=True)
class CompanyHistory:
    """The newest ``MAX_FILINGS`` filings and how many filings the company has in all."""

    filings: tuple[FilingFinancials, ...]
    total_filings: int


EPS_METRIC = "EPS_DILUTED"


def _current_point(current: EpsFactObservation) -> MetricPoint:
    fact = None
    if current.fact_id is not None:
        fact = EpsInputFact(
            fact_id=current.fact_id,
            accession_number=current.accession_number,
            tag=current.tag or "",
            taxonomy_version=current.taxonomy_version or "",
            unit=current.unit or "",
            period_end=current.period_end,  # type: ignore[arg-type]  # present with a fact
            duration_quarters=current.duration_quarters or 0,
            value=current.value,
            segments=current.segments or "",
            coreg=current.coreg or "",
        )
    return MetricPoint(PointStatus(current.status.value), current.reason, fact)


def _eps_comparison(observation: ComparativeEpsObservation) -> MetricComparison:
    change: EpsChangeObservation = compute_eps_change(observation)
    prior_status = (
        PointStatus.CURRENT_UNAVAILABLE
        if observation.status is ComparativeEpsStatus.CURRENT_UNAVAILABLE
        else PointStatus(observation.status.value.removeprefix("COMPARATIVE_"))
    )
    return MetricComparison(
        EPS_METRIC,
        _current_point(observation.current),
        MetricPoint(prior_status, observation.reason, observation.comparative_fact),
        MetricChange(
            status=change.status,
            absolute=change.eps_change,
            transition=change.transition,
            ratio=change.percentage_change,
            ratio_unavailable_reason=change.percentage_unavailable_reason,
        ),
    )


def _filing_financials(
    session: Session, company: Company, filing: CompanyFilingFacts
) -> FilingFinancials:
    repository = FinancialFactRepository(session)
    period_class = _PERIOD_CLASS[filing.form]
    comparisons: list[MetricComparison] = []
    for metric in MoneyMetric:
        facts = [
            EpsInputFact(
                fact_id=f.id,
                accession_number=filing.accession_number,
                tag=f.tag,
                taxonomy_version=f.version,
                unit=f.unit,
                period_end=f.period_end,
                duration_quarters=f.duration_quarters,
                value=f.value,
                segments=f.segments,
                coreg=f.coreg,
            )
            for f in repository.facts_for_filing_tag(filing.sec_filing_id, MONEY_TAG[metric])
        ]
        result = compare_money_metric(
            metric=metric,
            submission_period_ends=filing.submission_period_ends,
            facts=facts,
            period_class=period_class,
        )
        comparisons.append(
            MetricComparison(metric.value, result.current, result.prior, result.change)
        )
    comparisons.append(
        _eps_comparison(
            extract_comparative_eps_observation(
                session,
                filing.accession_number,
                period_class=period_class,
                eps_kind=EpsKind.DILUTED,
                company_cik=company.cik,
            )
        )
    )
    return FilingFinancials(filing, period_class, tuple(comparisons))


def build_company_financials(
    session: Session, company: Company, *, limit: int = MAX_FILINGS
) -> CompanyHistory:
    """The financials of the company's newest ``limit`` stored 10-Q/10-K-family filings."""
    filings = FinancialFactRepository(session).company_filings_with_facts(
        company.id, forms=FILING_FORMS
    )
    return CompanyHistory(
        filings=tuple(_filing_financials(session, company, f) for f in filings[:limit]),
        total_filings=len(filings),
    )
