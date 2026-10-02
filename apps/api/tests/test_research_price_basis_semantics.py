"""Price-basis semantics are stated correctly in the public research contract.

``PRICE_BASIS_NOTE`` is attached to every
:class:`~quantforge.research.event_study.EventStudyResult`, so it is itself
part of what this research layer promises a caller -- not an internal
comment. These tests check for the presence of the specific, load-bearing
claims (verified live against the real Massive API during the Step 8.7A/B
investigation), rather than asserting the note's exact wording, so a
copy-edit of the prose does not make this test suite brittle.
"""

from __future__ import annotations

from quantforge.research.event_study import PRICE_BASIS_NOTE


def test_the_note_states_split_adjustment() -> None:
    assert "split-adjusted" in PRICE_BASIS_NOTE


def test_the_note_states_dividends_are_excluded() -> None:
    assert "dividends" in PRICE_BASIS_NOTE.lower()
    assert "excluded" in PRICE_BASIS_NOTE.lower()


def test_the_note_never_claims_a_total_return() -> None:
    """Every sentence mentioning a total return must also disclaim it.

    Sentence-scoped rather than a bare substring check, so the note is free
    to be reworded without this test caring, as long as it never asserts a
    total return without a "not" in the same breath.
    """
    lowered = PRICE_BASIS_NOTE.lower()
    sentences_about_total_return = [
        sentence
        for sentence in lowered.split(".")
        if "total return" in sentence or "total-return" in sentence
    ]
    assert sentences_about_total_return, "expected the note to address total returns at all"
    for sentence in sentences_about_total_return:
        assert "not" in sentence, f"total return mentioned without a disclaimer: {sentence!r}"


def test_the_note_never_claims_the_abnormal_return_is_a_modelled_alpha() -> None:
    assert "not a capm alpha" in PRICE_BASIS_NOTE.lower()


def test_the_note_names_the_actual_price_fields() -> None:
    """Points a reader at the real columns, not a paraphrase of them."""
    assert "DailyPriceBar.close" in PRICE_BASIS_NOTE
    assert "BenchmarkPriceBar.close" in PRICE_BASIS_NOTE
