"""Same-accession comparative EPS: one defensible earlier fact, or an explicit refusal.

Given ONE accession and an explicit period class and EPS kind, this answers: "does the filing
present, next to its current EPS, exactly one defensible comparative EPS fact for the same concept
and duration one year earlier?" The value it returns is **comparative EPS as presented in this
filing**. It may be restated or reclassified and is NOT necessarily the EPS originally reported in
the prior-year filing; no other accession is ever consulted to reconcile it.

It is descriptive accounting interpretation only. No growth, percentage, absolute change, sign
transition, direction, score or signal exists here, and nothing says when a fact was known: no
availability field, no acceptance-time read, no clock.

How the comparative is identified (every stage is a refusal, never a guess)
---------------------------------------------------------------------------
1. **Current first.** The current observation is produced by the existing selector
   (:func:`quantforge.financials.eps.select_eps_fact`), so current semantics are defined once. If it
   is anything but ``AVAILABLE`` the result is ``CURRENT_UNAVAILABLE`` carrying that observation, and
   no earlier fact is looked at.
2. **Candidates are STORED facts, never generated dates.** Candidates are the supplied facts of the
   same accession with the exact current tag, a standard ``us-gaap/<year>`` version (any release; an
   extension or custom tag is never mapped), the same ``qtrs`` as the current fact (a year-to-date
   amount is never a quarter) and an earlier ``period_end``. No "prior-year date" is computed or
   looked up; the observed ``period_end`` values are inspected afterwards.
3. **Period relationship validates, it does not create.** Among those actual earlier facts, only a
   fact whose source-rounded period end lies exactly **12 month-ends** before the current one is
   accepted (``(cur.year - prior.year) * 12 + (cur.month - prior.month) == 12``, both dates being
   FSDS month-end dates). No day count, no tolerance, no fiscal-calendar inference. Several earlier
   annual columns are normal and not ambiguity: only the one at the validated relationship matters.
   This is a CONSERVATIVE identification rule, not an accounting truth: real FSDS shows 52/53-week
   filers whose rounded dates are 11 months apart and fiscal-year changes at 9/16/18/20 months. Those
   are refused (``COMPARATIVE_PERIOD_MISMATCH``), not repaired.
4. **Dimensions and co-registrants, unit, NULL, then exactly one.** The facts at the validated
   period are classified like the current selector does: only a fact with empty ``segments`` AND
   empty ``coreg`` is generic ("non-dimensional under the FSDS representation", never "consolidated");
   otherwise ``COMPARATIVE_DIMENSIONAL_ONLY`` / ``COMPARATIVE_COREG_ONLY``. Among the generic facts
   the unit must equal the current fact's (``USD``); only other units gives ``COMPARATIVE_WRONG_UNIT``
   (not "missing", no conversion). Then exactly one must remain: more than one is
   ``COMPARATIVE_AMBIGUOUS`` even with equal values or a NULL beside a number; a single NULL is
   ``COMPARATIVE_NULL_VALUE``. There is no tie-break of any kind.

Status pipeline (each status has one precise evidence meaning)
--------------------------------------------------------------
* ``CURRENT_UNAVAILABLE`` -- the current EPS is not ``AVAILABLE``; nothing earlier is read.
* ``COMPARATIVE_MISSING`` -- no *structurally relevant* earlier evidence: no stored fact of the same
  accession with the exact current tag, a standard us-gaap version, the same ``qtrs`` and an earlier
  period end. Facts of any other concept (basic, basic-and-diluted, continuing operations,
  custom/extension), any other ``qtrs`` or any other accession are never relevant, and never turn a
  missing comparative into a mismatch.
* ``COMPARATIVE_PERIOD_MISMATCH`` -- structurally relevant earlier evidence exists (whatever its
  dimensions, co-registrant or unit), but none is exactly 12 month-ends before the current period
  (or the current date is not a month end, which is reported as such).
* ``COMPARATIVE_DIMENSIONAL_ONLY`` / ``COMPARATIVE_COREG_ONLY`` -- at the validated period no generic
  fact exists, only dimensional / co-registrant ones.
* ``COMPARATIVE_WRONG_UNIT`` -- a generic fact exists at the validated period but none in the
  current unit (dimensional or co-registrant variants never mask it, in any order).
* ``COMPARATIVE_NULL_VALUE`` -- exactly one eligible fact, stating no value.
* ``COMPARATIVE_AMBIGUOUS`` -- more than one eligible fact.
* ``AVAILABLE`` -- exactly one eligible numeric fact.

The result never depends on the order the facts are supplied in.
"""

from __future__ import annotations

import calendar
import uuid
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Final

from quantforge.financials.eps import (
    EpsFactObservation,
    EpsInputFact,
    EpsKind,
    EpsPeriodClass,
    EpsStatus,
    is_generic_fact,
    is_us_gaap_version,
    select_eps_fact,
)
from quantforge.models.enums import SecFormType

__all__ = [
    "COMPARATIVE_MONTH_DISTANCE",
    "ComparativeEpsObservation",
    "ComparativeEpsStatus",
    "month_distance",
    "select_comparative_eps",
]

#: The conservative relationship that identifies a prior-year comparative: exactly this many
#: month-ends before the current period end. A validation applied to observed facts only.
COMPARATIVE_MONTH_DISTANCE: Final = 12


class ComparativeEpsStatus(StrEnum):
    """Why a comparative observation exists or why it was refused."""

    AVAILABLE = "AVAILABLE"
    #: The current EPS (10.6G semantics) is not AVAILABLE; its status is carried, nothing is searched.
    CURRENT_UNAVAILABLE = "CURRENT_UNAVAILABLE"
    #: No earlier standard same-concept, same-``qtrs`` fact is stored for this accession.
    COMPARATIVE_MISSING = "COMPARATIVE_MISSING"
    #: Earlier facts exist but none is exactly 12 month-ends before the current period end.
    COMPARATIVE_PERIOD_MISMATCH = "COMPARATIVE_PERIOD_MISMATCH"
    #: At the validated period only dimensional (axis/member, e.g. share-class) facts exist.
    COMPARATIVE_DIMENSIONAL_ONLY = "COMPARATIVE_DIMENSIONAL_ONLY"
    #: At the validated period only co-registrant facts exist.
    COMPARATIVE_COREG_ONLY = "COMPARATIVE_COREG_ONLY"
    #: The generic fact(s) at the validated period are not in the current fact's unit.
    COMPARATIVE_WRONG_UNIT = "COMPARATIVE_WRONG_UNIT"
    #: The single eligible comparative fact states no value (NULL). Not zero, not missing.
    COMPARATIVE_NULL_VALUE = "COMPARATIVE_NULL_VALUE"
    #: More than one eligible comparative fact remains. No tie-break, equal values not collapsed.
    COMPARATIVE_AMBIGUOUS = "COMPARATIVE_AMBIGUOUS"


def month_distance(current: date, prior: date) -> int:
    """Whole month positions from ``prior`` to ``current`` (year/month only; the day is ignored)."""
    return (current.year - prior.year) * 12 + (current.month - prior.month)


def _is_month_end(value: date) -> bool:
    return value.day == calendar.monthrange(value.year, value.month)[1]


def _satisfies_relationship(current_end: date, prior_end: date) -> bool:
    """Both dates are FSDS month-end dates and ``prior_end`` is exactly 12 month-ends earlier."""
    return (
        _is_month_end(current_end)
        and _is_month_end(prior_end)
        and month_distance(current_end, prior_end) == COMPARATIVE_MONTH_DISTANCE
    )


@dataclass(frozen=True, slots=True)
class ComparativeEpsObservation:
    """The current EPS observation plus the comparative fact identified beside it, or a refusal.

    ``current`` is the full 10.6G observation (always present; its status is why the result is
    ``CURRENT_UNAVAILABLE`` when it is). ``comparative_fact`` is the stored fact identified as
    "comparative EPS as presented in this filing" and is carried only for ``AVAILABLE`` and
    ``COMPARATIVE_NULL_VALUE``. There is deliberately no growth, change, direction, score,
    availability or signal field.
    """

    accession_number: str
    company_cik: str | None
    form: SecFormType | None
    period_class: EpsPeriodClass
    eps_kind: EpsKind
    status: ComparativeEpsStatus
    reason: str
    current: EpsFactObservation
    comparative_fact: EpsInputFact | None = None

    def __post_init__(self) -> None:
        if not self.reason:
            message = "a comparative observation must state a reason"
            raise ValueError(message)
        if self.current.accession_number != self.accession_number:
            message = "the current observation belongs to a different accession"
            raise ValueError(message)
        if (
            self.current.period_class is not self.period_class
            or self.current.eps_kind is not self.eps_kind
            or self.current.form != self.form
            or self.current.company_cik != self.company_cik
        ):
            message = "the current observation does not match the requested class, kind or filing"
            raise ValueError(message)
        current_available = self.current.status is EpsStatus.AVAILABLE
        if (self.status is ComparativeEpsStatus.CURRENT_UNAVAILABLE) == current_available:
            message = "CURRENT_UNAVAILABLE holds exactly when the current EPS is not AVAILABLE"
            raise ValueError(message)
        carries = self.status in (
            ComparativeEpsStatus.AVAILABLE,
            ComparativeEpsStatus.COMPARATIVE_NULL_VALUE,
        )
        if carries != (self.comparative_fact is not None):
            message = (
                f"{self.status} {'requires' if carries else 'must not carry'} a comparative fact"
            )
            raise ValueError(message)
        fact = self.comparative_fact
        if fact is None:
            return
        if fact.accession_number != self.accession_number:
            message = "the comparative fact belongs to a different accession"
            raise ValueError(message)
        if (
            self.current.period_end is None
            or fact.tag != self.current.tag
            or fact.duration_quarters != self.current.duration_quarters
            or not _satisfies_relationship(self.current.period_end, fact.period_end)
        ):
            message = "the comparative fact does not satisfy the validated period relationship"
            raise ValueError(message)
        if (
            not is_generic_fact(fact)
            or not is_us_gaap_version(fact.taxonomy_version)
            or fact.unit != self.current.unit
        ):
            message = (
                "the comparative fact must be generic, standard us-gaap and in the current unit"
            )
            raise ValueError(message)
        if self.status is ComparativeEpsStatus.AVAILABLE:
            if not isinstance(fact.value, Decimal) or not fact.value.is_finite():
                message = "AVAILABLE requires an exact finite Decimal comparative value"
                raise ValueError(message)
        elif fact.value is not None:
            message = "COMPARATIVE_NULL_VALUE must carry no value"
            raise ValueError(message)

    @classmethod
    def current_unavailable(  # noqa: PLR0913 - one keyword per identity field
        cls,
        *,
        accession_number: str,
        company_cik: str | None,
        form: SecFormType | None,
        period_class: EpsPeriodClass,
        eps_kind: EpsKind,
        current: EpsFactObservation,
    ) -> ComparativeEpsObservation:
        """The result when the current EPS is not AVAILABLE: nothing earlier is searched."""
        return cls(
            accession_number=accession_number,
            company_cik=company_cik,
            form=form,
            period_class=period_class,
            eps_kind=eps_kind,
            status=ComparativeEpsStatus.CURRENT_UNAVAILABLE,
            reason=f"the current EPS is {current.status.value}, so no comparative is searched: "
            f"{current.reason}",
            current=current,
        )

    @property
    def comparative_value(self) -> Decimal | None:
        """The comparative's exact source ``Decimal`` (``None`` unless ``AVAILABLE``)."""
        if self.status is not ComparativeEpsStatus.AVAILABLE or self.comparative_fact is None:
            return None
        return self.comparative_fact.value

    @property
    def comparative_fact_id(self) -> uuid.UUID | None:
        return None if self.comparative_fact is None else self.comparative_fact.fact_id

    @property
    def comparative_period_end(self) -> date | None:
        return None if self.comparative_fact is None else self.comparative_fact.period_end

    @property
    def period_month_distance(self) -> int | None:
        """Derived debugging aid: month positions between the two period ends. NOT authoritative
        evidence; the two stored period ends are."""
        if self.comparative_fact is None or self.current.period_end is None:
            return None
        return month_distance(self.current.period_end, self.comparative_fact.period_end)


def _dates(values: Collection[date], current_end: date) -> str:
    return ", ".join(
        f"{d.isoformat()} ({month_distance(current_end, d)} months before)" for d in sorted(values)
    )


def select_comparative_eps(  # noqa: PLR0911, PLR0913 - one return per named status; keyword-only request
    *,
    accession_number: str,
    company_cik: str | None,
    form: SecFormType,
    submission_period_ends: Collection[date],
    facts: Iterable[EpsInputFact],
    period_class: EpsPeriodClass,
    eps_kind: EpsKind,
) -> ComparativeEpsObservation:
    """The comparative EPS presented in ``accession_number`` beside its current EPS, or a refusal.

    ``facts`` must all belong to ``accession_number`` (a foreign fact raises ``ValueError``, as in
    the current selector). They are the facts of the requested concept's tag; every earlier fact is
    one that was actually stored, and nothing is computed from the current date to find one.
    """
    stored = list(facts)
    current = select_eps_fact(  # also rejects a foreign-accession fact
        accession_number=accession_number,
        company_cik=company_cik,
        form=form,
        submission_period_ends=submission_period_ends,
        facts=stored,
        period_class=period_class,
        eps_kind=eps_kind,
    )

    def result(
        status: ComparativeEpsStatus, reason: str, comparative: EpsInputFact | None = None
    ) -> ComparativeEpsObservation:
        return ComparativeEpsObservation(
            accession_number=accession_number,
            company_cik=company_cik,
            form=form,
            period_class=period_class,
            eps_kind=eps_kind,
            status=status,
            reason=reason,
            current=current,
            comparative_fact=comparative,
        )

    if current.status is not EpsStatus.AVAILABLE:
        return ComparativeEpsObservation.current_unavailable(
            accession_number=accession_number,
            company_cik=company_cik,
            form=form,
            period_class=period_class,
            eps_kind=eps_kind,
            current=current,
        )
    if (
        current.period_end is None
        or current.tag is None
        or current.duration_quarters is None
        or current.unit is None
    ):  # pragma: no cover - an AVAILABLE observation always carries its fact
        message = "an AVAILABLE current observation must carry its fact"
        raise RuntimeError(message)
    current_end = current.period_end
    duration = current.duration_quarters

    # Stage 1: actual earlier standard facts of the same concept and duration (no dates generated).
    earlier = [
        f
        for f in stored
        if f.tag == current.tag
        and is_us_gaap_version(f.taxonomy_version)
        and f.duration_quarters == duration
        and f.period_end < current_end
    ]
    if not earlier:
        extensions = sum(
            1
            for f in stored
            if f.tag == current.tag
            and not is_us_gaap_version(f.taxonomy_version)
            and f.duration_quarters == duration
            and f.period_end < current_end
        )
        ignored = (
            f"; {extensions} non-us-gaap-version fact(s) with the same name (e.g. issuer extensions) were ignored, never mapped"
            if extensions
            else ""
        )
        return result(
            ComparativeEpsStatus.COMPARATIVE_MISSING,
            f"{accession_number} stores no earlier us-gaap {current.tag} fact with qtrs={duration} "
            f"before {current_end.isoformat()}{ignored}; other durations and other EPS concepts "
            "are never substituted",
        )
    if not _is_month_end(current_end):
        return result(
            ComparativeEpsStatus.COMPARATIVE_PERIOD_MISMATCH,
            f"the current period end {current_end.isoformat()} is not an FSDS month-end date, so "
            f"the {COMPARATIVE_MONTH_DISTANCE}-month-end relationship cannot be validated against "
            "any earlier fact",
        )

    # Stage 2: of those observed facts, which satisfy the validated 12-month-end relationship?
    related = [f for f in earlier if _satisfies_relationship(current_end, f.period_end)]
    if not related:
        return result(
            ComparativeEpsStatus.COMPARATIVE_PERIOD_MISMATCH,
            f"earlier {current.tag} facts with qtrs={duration} exist at "
            f"{_dates({f.period_end for f in earlier}, current_end)}, but none is exactly "
            f"{COMPARATIVE_MONTH_DISTANCE} month-ends before {current_end.isoformat()}; "
            "such relationships (e.g. 52/53-week rounding, fiscal-calendar changes, or dates that "
            "are not month ends) are refused, not tolerated",
        )

    # Stage 3: dimensions / co-registrants at the validated period.
    generic = [f for f in related if is_generic_fact(f)]
    if not generic:
        if all(f.coreg != "" for f in related):
            return result(
                ComparativeEpsStatus.COMPARATIVE_COREG_ONLY,
                f"the {current.tag} facts {COMPARATIVE_MONTH_DISTANCE} month-ends earlier exist only "
                f"under a co-registrant ({len(related)} fact(s), "
                f"{len({f.coreg for f in related})} distinct coreg value(s)); none is treated as "
                "the filing company's EPS",
            )
        return result(
            ComparativeEpsStatus.COMPARATIVE_DIMENSIONAL_ONLY,
            f"the {current.tag} facts {COMPARATIVE_MONTH_DISTANCE} month-ends earlier exist only "
            f"with dimensions or co-registrants ({len(related)} fact(s), "
            f"{len({f.segments for f in related})} distinct segments string(s), "
            f"{len({f.coreg for f in related})} distinct coreg value(s)); none is non-dimensional "
            "under the FSDS representation, and no class or member is chosen",
        )

    # Stage 4: the unit must be the current fact's (evidence kept until now, so a wrong unit is
    # reported as such rather than as a missing comparative).
    eligible = [f for f in generic if f.unit == current.unit]
    if not eligible:
        units = ", ".join(sorted({f.unit for f in generic}))
        return result(
            ComparativeEpsStatus.COMPARATIVE_WRONG_UNIT,
            f"the non-dimensional {current.tag} comparative {COMPARATIVE_MONTH_DISTANCE} month-ends "
            f"earlier exists only in unit(s) {units}, not {current.unit}; units are never converted",
        )

    # Stage 5: exactly one, with no tie-break.
    if len(eligible) > 1:
        return result(
            ComparativeEpsStatus.COMPARATIVE_AMBIGUOUS,
            f"{len(eligible)} non-dimensional {current.unit} {current.tag} facts remain "
            f"{COMPARATIVE_MONTH_DISTANCE} month-ends before {current_end.isoformat()} (taxonomy "
            f"versions: {', '.join(sorted({f.taxonomy_version for f in eligible}))}); no tie-break "
            "is applied and equal values or a NULL beside a number are not resolved",
        )
    chosen = eligible[0]
    if chosen.value is None:
        return result(
            ComparativeEpsStatus.COMPARATIVE_NULL_VALUE,
            f"the one non-dimensional {current.tag} comparative at "
            f"{chosen.period_end.isoformat()} (version {chosen.taxonomy_version}) states no value",
            chosen,
        )
    return result(
        ComparativeEpsStatus.AVAILABLE,
        f"comparative EPS as presented in this filing: one non-dimensional {current.tag} fact "
        f"(version {chosen.taxonomy_version}) at {chosen.period_end.isoformat()}, "
        f"{COMPARATIVE_MONTH_DISTANCE} month-ends before {current_end.isoformat()}",
        chosen,
    )
