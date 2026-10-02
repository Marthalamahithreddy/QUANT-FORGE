"""Unit tests for the earnings exact-observation fingerprint.

The fingerprint is what makes "re-fetch the same fact" a no-op and "facts
changed" a new row, so its contract is narrow but load-bearing:

* deterministic -- the same observation digests to the same value, forever;
* canonical -- equal values spelled differently (``1.50`` vs ``1.5``, two
  offsets for one instant) still digest equal;
* sensitive -- every factual field participates, so any change is a new digest;
* ticker-free -- the issuer lives in the unique index key, not the digest.

These tests run against no database: the digest is pure, and its value is
checked against the persisted column in the integration layer.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

from quantforge.ingestion.canonical import CanonicalEarningsEvent
from quantforge.ingestion.earnings import earnings_fingerprint
from quantforge.models.earnings import earnings_event_fingerprint
from quantforge.models.enums import EarningsTiming


def _event(**overrides: object) -> CanonicalEarningsEvent:
    fields: dict[str, object] = {
        "ticker": "AAPL",
        "fiscal_year": 2026,
        "fiscal_quarter": 3,
        "event_date": date(2026, 9, 14),
        "event_timestamp": datetime(2026, 9, 14, 20, 30, tzinfo=UTC),
        "timing": EarningsTiming.AFTER_MARKET,
        "reported_eps": Decimal("2.35"),
        "estimated_eps": Decimal("2.20"),
        "reported_revenue": Decimal("95000000000"),
        "estimated_revenue": Decimal("94000000000"),
        "currency": "USD",
        "observed_at": None,
    }
    fields.update(overrides)
    return CanonicalEarningsEvent(**fields)  # type: ignore[arg-type]


def test_fingerprint_is_deterministic_and_a_sha256_hex_digest() -> None:
    # Two independently-constructed observations with equal facts must agree.
    assert earnings_fingerprint(_event()) == earnings_fingerprint(_event())
    digest = earnings_fingerprint(_event())
    assert len(digest) == 64
    assert all(character in "0123456789abcdef" for character in digest)


def test_equal_decimals_spelled_differently_collide() -> None:
    assert earnings_fingerprint(_event(reported_eps=Decimal("2.35"))) == earnings_fingerprint(
        _event(reported_eps=Decimal("2.3500"))
    )
    assert earnings_fingerprint(_event(reported_revenue=Decimal("100"))) == earnings_fingerprint(
        _event(reported_revenue=Decimal("1E+2"))
    )
    # A genuinely different value must not collide.
    assert earnings_fingerprint(_event(reported_eps=Decimal("2.35"))) != earnings_fingerprint(
        _event(reported_eps=Decimal("2.36"))
    )


def test_equal_instants_in_different_offsets_collide() -> None:
    utc = datetime(2026, 9, 14, 20, 30, tzinfo=UTC)
    est = datetime(2026, 9, 14, 16, 30, tzinfo=timezone(timedelta(hours=-4)))
    assert earnings_fingerprint(_event(event_timestamp=utc)) == earnings_fingerprint(
        _event(event_timestamp=est)
    )
    assert earnings_fingerprint(_event(event_timestamp=utc)) != earnings_fingerprint(
        _event(event_timestamp=datetime(2026, 9, 14, 20, 31, tzinfo=UTC))
    )


def test_every_factual_field_changes_the_fingerprint() -> None:
    base = earnings_fingerprint(_event())
    altered = {
        "reported_eps": Decimal("2.40"),
        "estimated_eps": Decimal("2.30"),
        "reported_revenue": Decimal("96000000000"),
        "estimated_revenue": Decimal("95000000000"),
        "fiscal_year": 2025,
        "fiscal_quarter": 4,
        "timing": EarningsTiming.BEFORE_MARKET,
        "currency": "CAD",
    }
    for field, value in altered.items():
        assert earnings_fingerprint(_event(**{field: value})) != base, field
    # Optional fields are part of the fact set: absent vs present is a change.
    assert (
        earnings_fingerprint(_event(observed_at=datetime(2026, 9, 13, 21, 0, tzinfo=UTC))) != base
    )
    assert earnings_fingerprint(_event(fiscal_quarter=None)) != base
    assert earnings_fingerprint(_event(currency=None)) != base
    assert earnings_fingerprint(_event(estimated_revenue=None)) != base


def test_fiscal_quarter_one_and_none_do_not_collide() -> None:
    """''no quarter'' and ''quarter 1'' are different facts, not one spellings."""
    assert earnings_fingerprint(_event(fiscal_quarter=1)) != earnings_fingerprint(
        _event(fiscal_quarter=None)
    )


def test_ticker_is_not_part_of_the_fingerprint() -> None:
    """Identity belongs to the unique index key, not the digest.

    Two issuers cannot share a ``(source_id, company_id, fingerprint)`` row, so
    the digest must stay issuer-free: it describes the *facts*, and which
    company they belong to is decided by resolution and enforced by the index.
    A digest that included the ticker would let the same facts dodge the unique
    key merely by being spelled with a different symbol.
    """
    assert earnings_fingerprint(_event(ticker="AAPL")) == earnings_fingerprint(
        _event(ticker="MSFT")
    )


def test_unknown_timing_values_are_not_part_of_the_contract() -> None:
    """The digest uses the canonical enum value, so spelling is the only form."""
    assert earnings_fingerprint(_event(timing=EarningsTiming.UNKNOWN)) != earnings_fingerprint(
        _event(timing=EarningsTiming.AFTER_MARKET)
    )


def test_model_and_canonical_derivations_agree() -> None:
    """The row derived at flush and the pipeline digest are one definition.

    The ingestion writer stamps ``earnings_event_fingerprint`` directly from
    the canonical fields, and the model recomputes the same function from the
    row's columns on every ORM write. If the two ever diverged, a revision
    would be mis-counted as a duplicate (or vice versa) purely because of which
    path wrote the row -- so they are pinned to the same digest here.
    """
    event = _event(
        event_timestamp=datetime(2026, 9, 14, 20, 30, tzinfo=timezone(timedelta(hours=-4)))
    )
    assert earnings_fingerprint(event) == earnings_event_fingerprint(
        fiscal_year=event.fiscal_year,
        fiscal_quarter=event.fiscal_quarter,
        event_date=event.event_date,
        event_timestamp=event.event_timestamp,
        timing=event.timing,
        reported_eps=event.reported_eps,
        estimated_eps=event.estimated_eps,
        reported_revenue=event.reported_revenue,
        estimated_revenue=event.estimated_revenue,
        currency=event.currency,
        observed_at=event.observed_at,
    )


# ── date-only observations (no exact instant) ───────────────────────────────


def test_a_date_only_event_with_no_timestamp_has_a_stable_fingerprint() -> None:
    """Same date, same facts, ``event_timestamp=None`` -- must still be stable."""
    assert earnings_fingerprint(_event(event_timestamp=None)) == earnings_fingerprint(
        _event(event_timestamp=None)
    )


def test_a_null_timestamp_never_collides_with_a_fabricated_midnight() -> None:
    """The exact failure mode this design exists to prevent.

    If a NULL timestamp ever hashed the same as some "invented" instant (say,
    midnight UTC on the event date), a date-only observation and a
    fabricated-precision one would be indistinguishable duplicates -- exactly
    the confusion of "we don't know" with "we know and it was midnight" this
    schema is built to keep apart.
    """
    midnight = datetime(2026, 9, 14, 0, 0, tzinfo=UTC)
    assert earnings_fingerprint(_event(event_timestamp=None)) != earnings_fingerprint(
        _event(event_timestamp=midnight)
    )


def test_a_different_exact_timestamp_on_the_same_date_changes_the_fingerprint() -> None:
    """Same ``event_date``, different ``event_timestamp`` -- must not collide.

    The date and the instant are independent facts in the digest; agreeing on
    one says nothing about the other.
    """
    same_date_a = _event(event_timestamp=datetime(2026, 9, 14, 20, 30, tzinfo=UTC))
    same_date_b = _event(event_timestamp=datetime(2026, 9, 14, 21, 45, tzinfo=UTC))
    assert earnings_fingerprint(same_date_a) != earnings_fingerprint(same_date_b)


def test_a_changed_event_date_changes_the_fingerprint_even_with_no_timestamp() -> None:
    """``event_date`` participates in the digest on its own, not merely as a
    byproduct of ``event_timestamp``."""
    assert earnings_fingerprint(
        _event(event_date=date(2026, 9, 14), event_timestamp=None)
    ) != earnings_fingerprint(_event(event_date=date(2026, 9, 15), event_timestamp=None))
