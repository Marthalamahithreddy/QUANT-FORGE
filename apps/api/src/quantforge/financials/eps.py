"""Accession-scoped EPS selection: one defensible standardized EPS fact, or an explicit refusal.

This is the first *semantic* layer over :class:`~quantforge.models.financial_fact.FinancialFact`.
It answers one question about ONE accession: "does this filing contain exactly one defensible
standardized EPS fact matching the requested accounting semantics?" It is descriptive accounting
extraction. It never says when a fact was known, and nothing here reads an acceptance time.

What it never does
------------------
* No availability, ``observed_at``, point-in-time eligibility or acceptance-time reading.
* No signal, direction, score, growth, surprise, return or execution concept.
* No cross-accession lookup: every fact handed in must belong to the requested accession (a
  foreign fact raises). An amendment is its own accession; the caller chooses which one to ask
  about, and nothing is superseded or preferred here.
* No Q4 derivation and no subtraction of year-to-date amounts.
* No float anywhere: values are the exact stored ``Decimal`` (FSDS itself caps them at four
  decimal places).

Selection (each stage is a refusal if nothing survives, never a guess)
-----------------------------------------------------------------------
1. **Form and period class.** The caller names the period class. 10-Q / 10-Q/A support only
   ``QUARTER``; 10-K / 10-K/A support only ``ANNUAL``. Anything else is refused. A 10-K quarter is
   never read or derived.
2. **Duration** is the stored ``duration_quarters`` (FSDS ``qtrs``): ``1`` for ``QUARTER``, ``4``
   for ``ANNUAL``. Year-to-date rows (``qtrs`` 2/3) are never a quarter. ``fy``/``fp`` and
   calendar months play no part.
3. **Period end.** The fact's ``period_end`` must equal the filing's own period end as the same
   FSDS submission states it. Both are rounded to a month end by the SEC with the same rule, so
   equality is the narrowest defensible check; no exact date is invented, and prior-period
   comparatives (a different period end) are never selected. If the stored submissions of the
   accession state different period ends the result is ``AMBIGUOUS``.
4. **Concept.** The primary concept is the standard ``EarningsPerShareDiluted``. It is matched by
   exact tag AND a ``us-gaap/<year>`` taxonomy version: no substring match, no hard-coded year
   (every historical release is accepted), and an issuer extension (whose version is an
   accession) is never treated as us-gaap. ``EarningsPerShareBasicAndDiluted`` is a separate,
   explicitly requested kind (:attr:`EpsKind.BASIC_AND_DILUTED`): the taxonomy defines it as net
   income per share "when basic and diluted earnings per share are the same amount and reported as
   a single line item", so it is a defensible observation under that label, never relabelled as
   ``DILUTED`` and never an automatic fallback. Basic EPS and every continuing-operations or
   other EPS concept are never substituted; only the requested concept's tag is considered.
5. **Dimensions.** Only a fact with empty ``segments`` AND empty ``coreg`` is eligible. That is
   described as *non-dimensional under the FSDS representation*, not "confirmed consolidated".
   Dimensional variants (for example share-class EPS) alongside one eligible fact are ignored;
   with no eligible fact the result is ``DIMENSIONAL_ONLY`` or ``COREG_ONLY`` and nothing is
   chosen from them. (In the pre-December-2024 FSDS layout dimensional rows were not compiled at
   all, so a filer with only class-specific EPS looks ``MISSING`` there, not ``DIMENSIONAL_ONLY``.)
6. **Unit**, checked on the eligible (non-dimensional) facts, must be exactly ``USD``. Real FSDS
   (2026q1) reports a per-share amount's unit as the bare currency code (``USD``; ``CAD`` also
   occurs; ``USD/shares`` was not observed in the sampled 2026q1 package), so the exact concept identifies the accounting measure
   and the unit only confirms the currency. Both are required: a ``USD`` fact is NOT EPS merely
   because of its unit. Any other unit, including ``CAD`` and ``USD/shares``, refuses
   (``WRONG_UNIT``); there is no alias list and no currency conversion.
7. **Exactly one.** Two or more eligible facts (for example two taxonomy releases in one accession)
   are ``AMBIGUOUS`` even if their values agree: there is no first/last/largest tie-break and no
   collapsing of equal values. A NULL value on the single eligible fact is ``NULL_VALUE``; it is
   not zero and not a missing row.

The result does not depend on the order facts are supplied in.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Final

from quantforge.models.enums import SecFormType
from quantforge.models.filing import ACCESSION_REGEX

__all__ = [
    "EPS_CONCEPT_TAG",
    "EPS_UNIT",
    "EpsFactObservation",
    "EpsInputFact",
    "EpsKind",
    "EpsPeriodClass",
    "EpsStatus",
    "select_eps_fact",
]

#: FSDS ``uom`` of a USD per-share EPS fact: the bare currency code (observed in the real 2026q1
#: package; other vintages are unsampled and would fail closed as WRONG_UNIT). The unit confirms the currency only; the exact EPS concept carries the meaning.
EPS_UNIT: Final = "USD"


class EpsPeriodClass(StrEnum):
    """The accounting period the caller wants. Always explicit; never guessed from the form."""

    #: A single fiscal quarter (``qtrs == 1``) of a 10-Q / 10-Q/A. Never year-to-date, never Q4.
    QUARTER = "QUARTER"
    #: A full fiscal year (``qtrs == 4``) of a 10-K / 10-K/A.
    ANNUAL = "ANNUAL"


class EpsKind(StrEnum):
    """Which standardized EPS concept is requested. Never substituted for one another."""

    DILUTED = "DILUTED"
    #: ``EarningsPerShareBasicAndDiluted``: one figure reported because basic equals diluted.
    BASIC_AND_DILUTED = "BASIC_AND_DILUTED"


class EpsStatus(StrEnum):
    """Why an extraction produced an observation or refused. Each refusal names its evidence."""

    AVAILABLE = "AVAILABLE"
    #: Nothing is stored for this accession (no filing, or no facts ingested). NOT "absent".
    NOT_INGESTED = "NOT_INGESTED"
    #: The form does not support the requested period class (or is not a 10-K/10-Q family form).
    UNSUPPORTED_FORM_PERIOD = "UNSUPPORTED_FORM_PERIOD"
    #: Stored accession, but no standardized fact of the concept at the requested duration/period.
    MISSING = "MISSING"
    #: Only an issuer extension with the same local name exists; it is never mapped to us-gaap.
    EXTENSION_ONLY = "EXTENSION_ONLY"
    WRONG_UNIT = "WRONG_UNIT"
    #: Only dimensional (axis/member, e.g. share-class) variants exist; none is chosen.
    DIMENSIONAL_ONLY = "DIMENSIONAL_ONLY"
    #: Only co-registrant variants exist; none is treated as the filing company's EPS.
    COREG_ONLY = "COREG_ONLY"
    #: The single eligible fact states no value (NULL). Not zero, not a missing row.
    NULL_VALUE = "NULL_VALUE"
    #: More than one candidate remains (or the filing identity/period is not unique).
    AMBIGUOUS = "AMBIGUOUS"


EPS_CONCEPT_TAG: Final[dict[EpsKind, str]] = {
    EpsKind.DILUTED: "EarningsPerShareDiluted",
    EpsKind.BASIC_AND_DILUTED: "EarningsPerShareBasicAndDiluted",
}
_PERIOD_FORMS: Final[dict[EpsPeriodClass, frozenset[SecFormType]]] = {
    EpsPeriodClass.QUARTER: frozenset({SecFormType.FORM_10Q, SecFormType.FORM_10Q_A}),
    EpsPeriodClass.ANNUAL: frozenset({SecFormType.FORM_10K, SecFormType.FORM_10K_A}),
}
#: FSDS ``qtrs``: whole quarters spanned. 1 = a quarter, 4 = a year.
_DURATION_QUARTERS: Final[dict[EpsPeriodClass, int]] = {
    EpsPeriodClass.QUARTER: 1,
    EpsPeriodClass.ANNUAL: 4,
}
#: FSDS ``version`` of a standard us-gaap tag: the taxonomy release (``us-gaap/2024``). Any
#: historical release matches; an issuer extension's version is an accession and never does.
_US_GAAP_VERSION: Final = re.compile(r"us-gaap/[0-9]{4}")


def _is_us_gaap_version(version: str) -> bool:
    return _US_GAAP_VERSION.fullmatch(version) is not None


@dataclass(frozen=True, slots=True)
class EpsInputFact:
    """The stored-fact fields the selector may look at, and nothing else.

    Deliberately carries no ingestion time, no filing acceptance and no artifact digest: those
    cannot influence a selection that is only about accounting meaning.
    """

    fact_id: uuid.UUID
    accession_number: str
    tag: str
    taxonomy_version: str
    unit: str
    period_end: date
    duration_quarters: int
    value: Decimal | None
    segments: str
    coreg: str


@dataclass(frozen=True, slots=True)
class EpsFactObservation:
    """One accession-scoped EPS observation, or the explicit reason there is none.

    Only ``AVAILABLE`` and ``NULL_VALUE`` carry a source fact (``fact_id`` and the fact fields);
    every other status carries none. ``value`` is the exact source ``Decimal`` (``None`` only for
    ``NULL_VALUE``). There is deliberately no availability, signal, score or comparison field.
    """

    accession_number: str
    company_cik: str | None
    form: SecFormType | None
    period_class: EpsPeriodClass
    eps_kind: EpsKind
    status: EpsStatus
    reason: str
    fact_id: uuid.UUID | None = None
    tag: str | None = None
    taxonomy_version: str | None = None
    unit: str | None = None
    period_end: date | None = None
    duration_quarters: int | None = None
    value: Decimal | None = None
    segments: str | None = None
    coreg: str | None = None

    def __post_init__(self) -> None:
        if not ACCESSION_REGEX.fullmatch(self.accession_number):
            message = f"accession_number must look like 0000320193-24-000123, got {self.accession_number!r}"
            raise ValueError(message)
        if not self.reason:
            message = "an observation must state a reason"
            raise ValueError(message)
        fact_fields = (
            self.fact_id,
            self.tag,
            self.taxonomy_version,
            self.unit,
            self.period_end,
            self.duration_quarters,
            self.segments,
            self.coreg,
        )
        carries_fact = self.status in (EpsStatus.AVAILABLE, EpsStatus.NULL_VALUE)
        present = [field is not None for field in fact_fields]
        if carries_fact and not all(present):
            message = f"{self.status} requires every fact field"
            raise ValueError(message)
        if not carries_fact and any(present):
            message = f"{self.status} must not carry a fact"
            raise ValueError(message)
        if not carries_fact and self.value is not None:
            message = f"{self.status} must not carry a value"
            raise ValueError(message)
        if self.status is EpsStatus.AVAILABLE:
            if not isinstance(self.value, Decimal) or not self.value.is_finite():
                message = "AVAILABLE requires an exact finite Decimal value"
                raise ValueError(message)
        elif self.status is EpsStatus.NULL_VALUE and self.value is not None:
            message = "NULL_VALUE must carry no value"
            raise ValueError(message)

    @classmethod
    def refusal(  # noqa: PLR0913 - one keyword per identity/evidence field
        cls,
        *,
        accession_number: str,
        company_cik: str | None,
        form: SecFormType | None,
        period_class: EpsPeriodClass,
        eps_kind: EpsKind,
        status: EpsStatus,
        reason: str,
    ) -> EpsFactObservation:
        """A result that carries no fact."""
        return cls(
            accession_number=accession_number,
            company_cik=company_cik,
            form=form,
            period_class=period_class,
            eps_kind=eps_kind,
            status=status,
            reason=reason,
        )


def _describe(facts: Collection[EpsInputFact]) -> str:
    """A deterministic, order-independent summary of candidate versions."""
    return ", ".join(sorted({f.taxonomy_version for f in facts}))


def select_eps_fact(  # noqa: PLR0911, PLR0913 - one return per named status; keyword-only request
    *,
    accession_number: str,
    company_cik: str | None,
    form: SecFormType,
    submission_period_ends: Collection[date],
    facts: Iterable[EpsInputFact],
    period_class: EpsPeriodClass,
    eps_kind: EpsKind,
) -> EpsFactObservation:
    """Select the one EPS fact of ``accession_number`` matching the request, or refuse.

    ``facts`` must all belong to ``accession_number`` (a foreign fact raises ``ValueError``: the
    selector never mixes accessions). ``submission_period_ends`` are the period ends the stored
    FSDS submission(s) of this accession state; empty means nothing was ingested.
    """
    candidates = list(facts)
    for fact in candidates:
        if fact.accession_number != accession_number:
            message = (
                f"fact {fact.fact_id} belongs to {fact.accession_number}, not the requested "
                f"{accession_number}; the selector never mixes accessions"
            )
            raise ValueError(message)

    def refuse(status: EpsStatus, reason: str) -> EpsFactObservation:
        return EpsFactObservation.refusal(
            accession_number=accession_number,
            company_cik=company_cik,
            form=form,
            period_class=period_class,
            eps_kind=eps_kind,
            status=status,
            reason=reason,
        )

    supported = _PERIOD_FORMS[period_class]
    if form not in supported:
        allowed = ", ".join(sorted(f.value for f in supported))
        return refuse(
            EpsStatus.UNSUPPORTED_FORM_PERIOD,
            f"form {form.value} does not support the {period_class.value} period class "
            f"(supported forms: {allowed}); no other period is read or derived",
        )

    period_ends = sorted(set(submission_period_ends))
    if not period_ends:
        return refuse(
            EpsStatus.NOT_INGESTED,
            f"no ingested submission for {accession_number}; this is not evidence the "
            "source states no EPS",
        )
    if len(period_ends) > 1:
        stated = ", ".join(d.isoformat() for d in period_ends)
        return refuse(
            EpsStatus.AMBIGUOUS,
            f"the stored submissions of {accession_number} state different period ends ({stated})",
        )
    period_end = period_ends[0]
    duration = _DURATION_QUARTERS[period_class]
    tag = EPS_CONCEPT_TAG[eps_kind]
    slot = [
        f
        for f in candidates
        if f.tag == tag and f.duration_quarters == duration and f.period_end == period_end
    ]

    standard = [f for f in slot if _is_us_gaap_version(f.taxonomy_version)]
    if not standard:
        extension = [f for f in slot if ACCESSION_REGEX.fullmatch(f.taxonomy_version)]
        if extension:
            return refuse(
                EpsStatus.EXTENSION_ONLY,
                f"only an issuer extension named {tag} exists for qtrs={duration} at "
                f"{period_end.isoformat()}; extensions are never mapped to us-gaap",
            )
        elsewhere = [
            f
            for f in candidates
            if f.tag == tag and _is_us_gaap_version(f.taxonomy_version) and f not in slot
        ]
        other = (
            f"; {len(elsewhere)} us-gaap {tag} fact(s) exist at other periods or durations and "
            "were not selected"
            if elsewhere
            else ""
        )
        return refuse(
            EpsStatus.MISSING,
            f"no us-gaap {tag} fact in {accession_number} matches the requested current period "
            f"(qtrs={duration}, period end {period_end.isoformat()}){other}; other EPS "
            "concepts are never substituted",
        )

    # Dimensions first: eligibility is about WHICH entity/context a fact describes, so a wrong
    # unit on the one entity-wide fact is reported as such, not hidden behind a variant.
    plain = [f for f in standard if f.segments == "" and f.coreg == ""]
    if not plain:
        if all(f.coreg != "" for f in standard):
            return refuse(
                EpsStatus.COREG_ONLY,
                f"us-gaap {tag} exists only under a co-registrant ({len(standard)} fact(s), "
                f"{len({f.coreg for f in standard})} distinct coreg value(s)); "
                "it is not treated as the filing company's EPS",
            )
        return refuse(
            EpsStatus.DIMENSIONAL_ONLY,
            f"us-gaap {tag} exists only with dimensions or co-registrants "
            f"({len(standard)} fact(s), {len({f.segments for f in standard})} distinct segments "
            f"string(s), {len({f.coreg for f in standard})} distinct coreg value(s)); none is "
            "non-dimensional under the FSDS representation, and no class or member is chosen",
        )

    eligible = [f for f in plain if f.unit == EPS_UNIT]
    if not eligible:
        units = ", ".join(sorted({f.unit for f in plain}))
        return refuse(
            EpsStatus.WRONG_UNIT,
            f"the non-dimensional us-gaap {tag} for qtrs={duration} at {period_end.isoformat()} "
            f"exists only in unit(s) {units}, not {EPS_UNIT}",
        )

    if len(eligible) > 1:
        return refuse(
            EpsStatus.AMBIGUOUS,
            f"{len(eligible)} non-dimensional us-gaap {tag} facts remain for qtrs={duration} at "
            f"{period_end.isoformat()} (taxonomy versions: {_describe(eligible)}); no tie-break "
            "is applied and equal values are not collapsed",
        )

    chosen = eligible[0]
    status = EpsStatus.AVAILABLE if chosen.value is not None else EpsStatus.NULL_VALUE
    reason = (
        f"one non-dimensional us-gaap {tag} fact (version {chosen.taxonomy_version}) for "
        f"qtrs={duration} at {period_end.isoformat()}"
        if chosen.value is not None
        else f"the one non-dimensional us-gaap {tag} fact (version {chosen.taxonomy_version}) "
        f"for qtrs={duration} at {period_end.isoformat()} states no value"
    )
    return EpsFactObservation(
        accession_number=accession_number,
        company_cik=company_cik,
        form=form,
        period_class=period_class,
        eps_kind=eps_kind,
        status=status,
        reason=reason,
        fact_id=chosen.fact_id,
        tag=chosen.tag,
        taxonomy_version=chosen.taxonomy_version,
        unit=chosen.unit,
        period_end=chosen.period_end,
        duration_quarters=chosen.duration_quarters,
        value=chosen.value,
        segments=chosen.segments,
        coreg=chosen.coreg,
    )
