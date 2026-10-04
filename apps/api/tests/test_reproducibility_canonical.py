"""Canonical serialisation, fingerprints, methodology and structural isolation (no database)."""

from __future__ import annotations

import ast
import dataclasses
import inspect
import json
import uuid
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

import quantforge
from quantforge.models.enums import AvailabilityBasis, ReproducibilityGap, ReproducibilityLevel
from quantforge.reproducibility.baseline import ReproducibilityBaseline
from quantforge.reproducibility.canonical import (
    Fingerprint,
    canonical_bytes,
    canonical_text,
    canonical_value,
    fingerprint_of,
    fingerprint_of_text,
)
from quantforge.reproducibility.manifest import ReproducibilityManifest, ReproducibilityRecord
from quantforge.reproducibility.methodology import (
    COMPONENT_VERSIONS,
    CURRENT_METHODOLOGY,
    current_methodology,
)
from quantforge.research.alignment import ALIGNMENT_WINDOW_DAYS, align_earnings_event
from quantforge.research.event_returns import forward_price_window_end

SRC = Path(quantforge.__file__).resolve().parent
PACKAGE = sorted((SRC / "reproducibility").glob("*.py"))


def test_object_key_order_never_changes_the_bytes() -> None:
    a = {"b": 1, "a": {"y": 2, "x": 3}}
    b = {"a": {"x": 3, "y": 2}, "b": 1}
    assert canonical_bytes(a) == canonical_bytes(b) == b'{"a":{"x":3,"y":2},"b":1}'


def test_sequence_order_is_preserved_because_it_can_be_meaningful() -> None:
    assert canonical_bytes([1, 5, 20]) != canonical_bytes([20, 5, 1])


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (Decimal("0"), "0"),
        (Decimal("-0.5"), "-0.5"),
        (Decimal("1.5300"), "1.5300"),
        (Decimal("1.5"), "1.5"),
        (Decimal("1E-7"), "0.0000001"),
        (Decimal("1E+3"), "1000"),
        (Decimal("123456789012345678.9012"), "123456789012345678.9012"),
    ],
)
def test_decimals_are_fixed_point_text_and_keep_their_scale(value: Decimal, text: str) -> None:
    assert canonical_value(value) == text
    assert canonical_bytes(Decimal("1.50")) != canonical_bytes(Decimal("1.5")), (
        "stored scale is domain data: trailing zeros are preserved"
    )


@pytest.mark.parametrize("bad", [Decimal("NaN"), Decimal("Infinity"), 1.5, float("nan")])
def test_non_finite_decimals_and_floats_are_rejected(bad: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        canonical_value(bad)


def test_datetimes_are_utc_and_naive_ones_are_rejected() -> None:
    eastern = timezone(timedelta(hours=-5))
    assert (
        canonical_value(datetime(2026, 3, 2, 7, 0, tzinfo=eastern))
        == canonical_value(datetime(2026, 3, 2, 12, 0, tzinfo=UTC))
        == "2026-03-02T12:00:00.000000Z"
    )
    with pytest.raises(ValueError, match="naive"):
        canonical_value(datetime(2026, 3, 2, 12, 0))  # noqa: DTZ001
    assert canonical_value(date(2026, 3, 2)) == "2026-03-02"


def test_enums_uuids_none_and_unknown_types() -> None:
    ident = uuid.UUID("12345678-1234-5678-1234-567812345678")
    assert canonical_value(AvailabilityBasis.SOURCE_PUBLICATION) == "SOURCE_PUBLICATION"
    assert canonical_value(ident) == str(ident)
    assert canonical_value(None) is None
    with pytest.raises(TypeError):
        canonical_value(object())
    with pytest.raises(TypeError):
        canonical_value({1: "x"})
    with pytest.raises(TypeError):
        canonical_value({"s": {1, 2}})


def test_bytes_are_ascii_json_without_whitespace_and_fingerprint_is_sha256() -> None:
    payload = {"é": "ü"}
    raw = canonical_bytes(payload)
    assert raw == b'{"\\u00e9":"\\u00fc"}'
    assert json.loads(raw) == payload
    fp = fingerprint_of(payload)
    assert fp.algorithm == "sha256" and len(fp.digest) == 64
    assert fp == fingerprint_of({"é": "ü"})
    with pytest.raises(ValueError, match="sha256"):
        Fingerprint("md5", "0" * 64)


def test_methodology_changes_the_identity_even_when_nothing_numeric_changes() -> None:
    base = fingerprint_of(CURRENT_METHODOLOGY)
    assert fingerprint_of(current_methodology()) == base
    bumped = current_methodology({"event_study": "event-study/2"})
    assert fingerprint_of(bumped) != base


def test_methodology_table_is_pinned_so_a_semantic_edit_is_deliberate() -> None:
    """Changing a label here is the deliberate act that records a semantic change."""
    assert COMPONENT_VERSIONS == {
        "observation_contract": "research-observation/1",
        "event_study": "event-study/1",
        "session_alignment": "stored-bar-sessions/1",
        "price_series": "coherent-single-source-series/1",
        "forward_returns": "close-to-close-session-returns/1",
        "benchmark_returns": "matched-endpoint-benchmark/1",
        "abnormal_returns": "market-adjusted-simple/1",
        "listing_resolution": "listing-resolution/1",
        "availability_policy": "availability-policy/1",
        "eps_surprise": "eps-surprise/1",
        "filing_eps_selection": "filing-eps-selection/1",
        "eps_change": "eps-change/1",
        "manifest_schema": "reproducibility-manifest/2",
    }
    assert [b.value for b in CURRENT_METHODOLOGY.approved_availability_bases] == [
        "SOURCE_PUBLICATION"
    ], "approving a new availability basis changes the methodology identity"


def _evidence(**overrides: object) -> dict[str, object]:
    run = "11111111-1111-1111-1111-111111111111"
    evidence: dict[str, object] = {
        "event": {"ingestion_run_id": run},
        "stock": {"bars": [{"ingestion_run_id": run}]},
        "benchmark": {"bars": []},
        "filing": {"state": "NOT_REQUESTED", "ingestion_run_id": None},
        "accounting": {"state": "NOT_REQUESTED", "artifacts": []},
    }
    evidence.update(overrides)
    return evidence


def _baseline(evidence: dict[str, object]) -> ReproducibilityBaseline:
    manifest = {"request": {}, "methodology": {}, "evidence": evidence}
    return ReproducibilityBaseline.from_canonical(
        canonical_text(manifest), canonical_text({"r": 1})
    )


def test_the_level_is_derived_from_the_evidence_and_cannot_be_set() -> None:
    assert _baseline(_evidence()).level is ReproducibilityLevel.COMPLETE
    bar = {"ingestion_run_id": None}
    partial = _baseline(_evidence(benchmark={"bars": [bar]}))
    assert partial.gaps == (ReproducibilityGap.BENCHMARK_BARS_WITHOUT_INGESTION_RUN,)
    assert partial.level is ReproducibilityLevel.PARTIAL
    names = {f.name for f in dataclasses.fields(ReproducibilityBaseline)}
    assert "level" not in names and "level" not in {
        f.name for f in dataclasses.fields(ReproducibilityRecord)
    }


def test_a_benchmark_bar_with_a_run_adds_no_gap_and_an_unrequested_benchmark_none() -> None:
    run = "22222222-2222-2222-2222-222222222222"
    assert _baseline(_evidence(benchmark={"bars": [{"ingestion_run_id": run}]})).gaps == ()
    assert _baseline(_evidence(benchmark={"bars": []})).gaps == ()


def test_every_gap_rule_fires_only_on_consumed_unpinned_evidence() -> None:
    gaps = _baseline(
        _evidence(
            event={"ingestion_run_id": None},
            stock={"bars": [{"ingestion_run_id": None}]},
            filing={"state": "PINNED", "ingestion_run_id": None},
            accounting={"state": "PINNED", "artifacts": []},
        )
    ).gaps
    assert gaps == (
        ReproducibilityGap.EVENT_WITHOUT_INGESTION_RUN,
        ReproducibilityGap.STOCK_BAR_WITHOUT_INGESTION_RUN,
        ReproducibilityGap.FILING_WITHOUT_INGESTION_RUN,
        ReproducibilityGap.ACCOUNTING_NOT_INGESTED,
    )
    unread = _baseline(
        _evidence(
            filing={"state": "NOT_READ", "ingestion_run_id": None},
            accounting={"state": "NOT_READ", "artifacts": []},
        )
    )
    assert unread.gaps == ()


def test_the_baseline_rejects_non_canonical_text_and_derives_digests_from_the_text() -> None:
    text = canonical_text({"request": {}, "methodology": {}, "evidence": _evidence()})
    pretty = json.dumps(json.loads(text), indent=2)
    with pytest.raises(ValueError, match="not in canonical form"):
        ReproducibilityBaseline.from_canonical(pretty, canonical_text({"r": 1}))
    with pytest.raises(ValueError, match="not valid canonical JSON"):
        ReproducibilityBaseline.from_canonical("{", canonical_text({"r": 1}))
    with pytest.raises(ValueError, match="request, methodology and evidence"):
        ReproducibilityBaseline.from_canonical(canonical_text({"x": 1}), canonical_text({"r": 1}))
    baseline = ReproducibilityBaseline.from_canonical(text, canonical_text({"r": 1}))
    assert baseline.fingerprints.manifest == fingerprint_of_text(text)
    assert baseline.fingerprints.evidence == fingerprint_of(json.loads(text)["evidence"])


def test_methodology_overrides_reject_unknown_components_and_windows_follow_the_engines() -> None:
    with pytest.raises(ValueError, match="unknown methodology"):
        current_methodology({"not_a_component": "x/1"})
    # the pinned windows must be the ones the engines actually use
    default = inspect.signature(align_earnings_event).parameters["window_days"].default
    assert default == ALIGNMENT_WINDOW_DAYS == CURRENT_METHODOLOGY.alignment_window_days
    anchor = date(2026, 3, 2)
    assert forward_price_window_end(anchor, [1]) == anchor + timedelta(days=14)
    assert forward_price_window_end(anchor, [20]) == anchor + timedelta(days=60)


def _imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


@pytest.mark.parametrize("path", PACKAGE, ids=lambda p: p.name)
def test_reproducibility_is_pure_read_only_clockless_and_arithmetic_free(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    forbidden = (
        "quantforge.providers",
        "quantforge.ingestion",
        "quantforge.signals",
        "quantforge.backtest",
        "quantforge.api",
        "httpx",
        "requests",
        "socket",
        "urllib",
        "random",
        "time",
        "subprocess",
    )
    assert not {
        n for n in _imports(path) if any(n == f or n.startswith(f"{f}.") for f in forbidden)
    }
    for token in (".commit(", ".add(", ".flush(", ".delete(", "now(", "utcnow", "today(", "hash("):
        assert token not in text, (path.name, token)
    calls = {
        n.func.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "float" not in calls and "hash" not in calls and "repr" not in calls
    arithmetic = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.BinOp)
        and isinstance(n.op, (ast.Mult, ast.Div, ast.Sub))
        and not isinstance(n.left, (ast.Constant, ast.Tuple, ast.List))
    ]
    # the only subtraction allowed is calendar-date window arithmetic (date - timedelta)
    for node in arithmetic:
        assert isinstance(node.op, ast.Sub) and isinstance(node.right, ast.Call), ast.dump(node)


def test_no_git_sha_or_package_version_is_used_as_methodology() -> None:
    for path in PACKAGE:
        text = path.read_text(encoding="utf-8")
        for token in ("git rev", "importlib.metadata", "__version__", "subprocess"):
            assert token not in text


def test_the_manifest_fingerprint_covers_request_methodology_and_evidence_only() -> None:
    assert {f.name for f in dataclasses.fields(ReproducibilityManifest)} == {
        "request",
        "methodology",
        "evidence",
    }, "level, gaps and the result are not part of the manifest identity"
