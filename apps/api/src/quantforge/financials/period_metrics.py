"""Revenue and net income for one filing: the current figure beside its prior-year comparative.

The same evidence rules as current/comparative EPS (:mod:`quantforge.financials.eps`,
:mod:`quantforge.financials.comparative_eps`), applied to two USD flow concepts. Pure functions over
facts of ONE accession; nothing here reads a database, another accession or a clock.

* A fact is eligible only if it is a standard us-gaap tag (an issuer extension is never mapped), has
  empty ``segments`` AND empty ``coreg`` ("non-dimensional under the FSDS representation", never
  "confirmed consolidated") and is in ``USD`` (units are never converted).
* Current = the fact for the filing's own period end and the explicit period class (``qtrs`` 1 for a
  10-Q, 4 for a 10-K). Year-to-date amounts are never used.
* Prior = an actually stored earlier fact of the same concept and ``qtrs``, exactly 12 month-ends
  before the current period end. It is "as presented in this filing" and may be restated; no other
  accession is consulted. No date is generated and nothing is tolerated (52/53-week filers are
  refused as PERIOD_MISMATCH rather than repaired).
* More than one eligible fact is AMBIGUOUS even when the values are equal; there is no tie-break.

``PointStatus`` is the union of the EPS current and comparative statuses so that all three metrics
(including EPS, which keeps using the frozen EPS selectors) are reported in one vocabulary.
"""

from __future__ import annotations

import calendar
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, DecimalException
from enum import StrEnum
from typing import Final

from quantforge.financials.comparative_eps import COMPARATIVE_MONTH_DISTANCE, month_distance
from quantforge.financials.eps import (
    EpsInputFact,
    EpsPeriodClass,
    is_generic_fact,
    is_us_gaap_version,
)
from quantforge.financials.eps_change import (
    EpsChangeStatus,
    EpsTransition,
    PercentageUnavailableReason,
    classify_transition,
    eps_change_context,
)
from quantforge.models.filing import ACCESSION_REGEX

__all__ = [
    "MONEY_TAG",
    "MONEY_UNIT",
    "MetricChange",
    "MetricPoint",
    "MoneyComparison",
    "MoneyMetric",
    "PointStatus",
    "compare_money_metric",
    "money_change",
]

MONEY_UNIT: Final = "USD"
_DURATION_QUARTERS: Final[dict[EpsPeriodClass, int]] = {
    EpsPeriodClass.QUARTER: 1,
    EpsPeriodClass.ANNUAL: 4,
}


class MoneyMetric(StrEnum):
    REVENUE = "REVENUE"
    NET_INCOME = "NET_INCOME"


#: One exact standard tag per metric. A different revenue concept is MISSING, never substituted.
MONEY_TAG: Final[dict[MoneyMetric, str]] = {
    MoneyMetric.REVENUE: "RevenueFromContractWithCustomerExcludingAssessedTax",
    MoneyMetric.NET_INCOME: "NetIncomeLoss",
}


class PointStatus(StrEnum):
    """Why a figure is shown or why there is none. Each refusal names its own evidence."""

    AVAILABLE = "AVAILABLE"
    NOT_INGESTED = "NOT_INGESTED"
    UNSUPPORTED_FORM_PERIOD = "UNSUPPORTED_FORM_PERIOD"
    MISSING = "MISSING"
    EXTENSION_ONLY = "EXTENSION_ONLY"
    WRONG_UNIT = "WRONG_UNIT"
    DIMENSIONAL_ONLY = "DIMENSIONAL_ONLY"
    COREG_ONLY = "COREG_ONLY"
    NULL_VALUE = "NULL_VALUE"
    AMBIGUOUS = "AMBIGUOUS"
    #: Earlier facts exist but none is exactly 12 month-ends before the current period.
    PERIOD_MISMATCH = "PERIOD_MISMATCH"
    #: The comparative was not looked for because the current figure is not available.
    CURRENT_UNAVAILABLE = "CURRENT_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class MetricPoint:
    """One figure, or the reason there is none. Only AVAILABLE and NULL_VALUE carry a fact."""

    status: PointStatus
    reason: str
    fact: EpsInputFact | None = None


@dataclass(frozen=True, slots=True)
class MetricChange:
    """``current - prior`` (exact) and, for a positive prior, ``change / prior`` as a ratio."""

    status: EpsChangeStatus
    absolute: Decimal | None = None
    transition: EpsTransition | None = None
    ratio: Decimal | None = None
    ratio_unavailable_reason: PercentageUnavailableReason | None = None


@dataclass(frozen=True, slots=True)
class MoneyComparison:
    metric: MoneyMetric
    current: MetricPoint
    prior: MetricPoint
    change: MetricChange


def _is_month_end(value: date) -> bool:
    return value.day == calendar.monthrange(value.year, value.month)[1]


def _eligible_point(  # noqa: PLR0911 - one return per named refusal
    slot: list[EpsInputFact], tag: str, where: str
) -> MetricPoint:
    """The one eligible fact of ``slot`` (same tag, duration and period), or a named refusal."""
    standard = [f for f in slot if is_us_gaap_version(f.taxonomy_version)]
    if not standard:
        if any(ACCESSION_REGEX.fullmatch(f.taxonomy_version) for f in slot):
            return MetricPoint(
                PointStatus.EXTENSION_ONLY,
                f"only an issuer extension named {tag} exists {where}; extensions are never "
                "mapped to us-gaap",
            )
        return MetricPoint(PointStatus.MISSING, f"no us-gaap {tag} fact {where}")
    plain = [f for f in standard if is_generic_fact(f)]
    if not plain:
        if all(f.coreg != "" for f in standard):
            return MetricPoint(
                PointStatus.COREG_ONLY,
                f"us-gaap {tag} exists {where} only under a co-registrant; it is not treated as "
                "the filing company's figure",
            )
        return MetricPoint(
            PointStatus.DIMENSIONAL_ONLY,
            f"us-gaap {tag} exists {where} only with dimensions or co-registrants; none is "
            "non-dimensional and no member is chosen",
        )
    usable = [f for f in plain if f.unit == MONEY_UNIT]
    if not usable:
        units = ", ".join(sorted({f.unit for f in plain}))
        return MetricPoint(
            PointStatus.WRONG_UNIT,
            f"the non-dimensional {tag} exists {where} only in unit(s) {units}, not "
            f"{MONEY_UNIT}; units are never converted",
        )
    if len(usable) > 1:
        return MetricPoint(
            PointStatus.AMBIGUOUS,
            f"{len(usable)} non-dimensional {MONEY_UNIT} {tag} facts remain {where}; no tie-break "
            "is applied and equal values are not collapsed",
        )
    chosen = usable[0]
    if chosen.value is None:
        return MetricPoint(
            PointStatus.NULL_VALUE,
            f"the one non-dimensional {tag} fact {where} states no value",
            chosen,
        )
    return MetricPoint(
        PointStatus.AVAILABLE,
        f"one non-dimensional {MONEY_UNIT} us-gaap {tag} fact (version {chosen.taxonomy_version}) "
        f"{where}",
        chosen,
    )


def money_change(current: MetricPoint, prior: MetricPoint) -> MetricChange:
    """Exact ``current - prior`` and its ratio, only when both figures are AVAILABLE."""
    if (
        current.status is not PointStatus.AVAILABLE
        or prior.status is not PointStatus.AVAILABLE
        or current.fact is None
        or prior.fact is None
        or current.fact.value is None
        or prior.fact.value is None
    ):
        return MetricChange(EpsChangeStatus.INPUT_UNAVAILABLE)
    now, before = current.fact.value, prior.fact.value
    try:
        difference = eps_change_context(exact=True).subtract(now, before)
        if difference == 0:
            difference = abs(difference)  # -0 -> +0
        ratio: Decimal | None = None
        reason: PercentageUnavailableReason | None = None
        if before > 0:
            ratio = eps_change_context().divide(difference, before)
        elif before == 0:
            reason = PercentageUnavailableReason.ZERO_COMPARATIVE
        else:
            reason = PercentageUnavailableReason.NEGATIVE_COMPARATIVE
    except DecimalException as exc:
        message = f"values outside the exact arithmetic envelope: {type(exc).__name__}"
        raise ValueError(message) from exc
    return MetricChange(
        EpsChangeStatus.AVAILABLE,
        absolute=difference,
        transition=classify_transition(current=now, comparative=before),
        ratio=ratio,
        ratio_unavailable_reason=reason,
    )


def compare_money_metric(
    *,
    metric: MoneyMetric,
    submission_period_ends: Collection[date],
    facts: Iterable[EpsInputFact],
    period_class: EpsPeriodClass,
) -> MoneyComparison:
    """The current and prior-year figure of ``metric`` presented in one filing, or named refusals.

    ``facts`` are the filing's stored facts of the metric's tag; ``submission_period_ends`` are the
    period ends its FSDS submission(s) state (empty: nothing ingested; several: ambiguous).
    """
    tag = MONEY_TAG[metric]
    stored = [f for f in facts if f.tag == tag]
    duration = _DURATION_QUARTERS[period_class]
    period_ends = sorted(set(submission_period_ends))

    def refused(point: MetricPoint) -> MoneyComparison:
        unavailable = MetricPoint(
            PointStatus.CURRENT_UNAVAILABLE, "the current figure is not available"
        )
        return MoneyComparison(
            metric, point, unavailable, MetricChange(EpsChangeStatus.INPUT_UNAVAILABLE)
        )

    if not period_ends:
        return refused(
            MetricPoint(PointStatus.NOT_INGESTED, "no ingested submission for this filing")
        )
    if len(period_ends) > 1:
        return refused(
            MetricPoint(
                PointStatus.AMBIGUOUS,
                "the stored submissions of this filing state different period ends",
            )
        )
    current_end = period_ends[0]
    where = f"for qtrs={duration} at {current_end.isoformat()}"
    current = _eligible_point(
        [f for f in stored if f.duration_quarters == duration and f.period_end == current_end],
        tag,
        where,
    )
    if current.status is not PointStatus.AVAILABLE:
        return refused(current)

    earlier = [
        f
        for f in stored
        if is_us_gaap_version(f.taxonomy_version)
        and f.duration_quarters == duration
        and f.period_end < current_end
    ]
    if not earlier:
        prior = MetricPoint(
            PointStatus.MISSING,
            f"this filing stores no earlier us-gaap {tag} fact with qtrs={duration}",
        )
    else:
        related = [
            f
            for f in earlier
            if _is_month_end(current_end)
            and _is_month_end(f.period_end)
            and month_distance(current_end, f.period_end) == COMPARATIVE_MONTH_DISTANCE
        ]
        if not related:
            prior = MetricPoint(
                PointStatus.PERIOD_MISMATCH,
                f"earlier {tag} facts exist, but none is exactly {COMPARATIVE_MONTH_DISTANCE} "
                f"month-ends before {current_end.isoformat()}; other relationships are refused, "
                "not tolerated",
            )
        else:
            prior = _eligible_point(
                related, tag, f"{COMPARATIVE_MONTH_DISTANCE} month-ends before {current_end}"
            )
    return MoneyComparison(metric, current, prior, money_change(current, prior))
