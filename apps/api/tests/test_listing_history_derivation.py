"""Pure derivation of ticker eras from sourced facts and first-observed listings.

The invariants under test: nothing is claimed before the evidence, gaps stay gaps, two dated
observations never become a continuous interval, sources never outrank one another, and the
result is a deterministic function of the inputs (never of their order).
"""

from __future__ import annotations

import random
import uuid
from datetime import date, timedelta

import pytest

from quantforge.identity.listing_history import (
    Evidence,
    FactView,
    ProvisionalListingView,
    derive_security_history,
)
from quantforge.models.enums import ListingFactKind

SECURITY = uuid.uuid4()
MASSIVE = uuid.UUID(int=1)
OTHER = uuid.UUID(int=2)
D = date

STARTED = ListingFactKind.TICKER_STARTED
OBSERVED = ListingFactKind.TICKER_OBSERVED
LAST = ListingFactKind.LAST_TRADED


def fact(kind: ListingFactKind, ticker: str, on: date, source: uuid.UUID = MASSIVE) -> FactView:
    return FactView(source_id=source, kind=kind, ticker=ticker, effective_date=on)


def listing(
    ticker: str, valid_from: date, valid_to: date | None = None, *, primary: bool = True
) -> ProvisionalListingView:
    return ProvisionalListingView(
        listing_id=uuid.uuid4(),
        ticker=ticker,
        mic="XNAS",
        valid_from=valid_from,
        valid_to=valid_to,
        is_primary=primary,
    )


FB_META = [fact(STARTED, "FB", D(2012, 5, 18)), fact(STARTED, "META", D(2022, 6, 9))]


def test_a_provisional_listing_alone_proves_only_from_its_first_observed_date() -> None:
    history = derive_security_history(SECURITY, [], [listing("AAPL", D(2026, 10, 2))])

    assert history.sourced_eras == ()
    assert history.provisional_tickers_on(D(2026, 10, 2)) == {"AAPL"}
    assert history.provisional_tickers_on(D(2026, 10, 1)) == frozenset()


def test_ticker_events_prove_each_era_from_its_start_and_stop_at_the_next_start() -> None:
    history = derive_security_history(SECURITY, FB_META, [listing("META", D(2026, 10, 2))])

    assert history.sourced_tickers_on(D(2012, 5, 17)) == frozenset(), (
        "nothing before the first event"
    )
    assert history.sourced_tickers_on(D(2012, 5, 18)) == {"FB"}, "start is inclusive"
    assert history.sourced_tickers_on(D(2022, 6, 8)) == {"FB"}
    assert history.sourced_tickers_on(D(2022, 6, 9)) == {"META"}, (
        "the next start is exclusive for FB"
    )
    assert history.sourced_tickers_on(D(2030, 1, 1)) == {"META"}, (
        "open only because a listing corroborates"
    )


def test_the_last_era_is_not_open_ended_without_corroboration() -> None:
    history = derive_security_history(SECURITY, FB_META, [])

    assert history.sourced_tickers_on(D(2022, 6, 9)) == {"META"}, "the start day itself is proven"
    assert history.sourced_tickers_on(D(2022, 6, 10)) == frozenset(), "the rest is NOT claimed"


def test_the_last_era_extends_to_its_latest_dated_observation_only() -> None:
    facts = [*FB_META, fact(OBSERVED, "META", D(2023, 1, 3))]
    history = derive_security_history(SECURITY, facts, [])

    assert history.sourced_tickers_on(D(2023, 1, 3)) == {"META"}
    assert history.sourced_tickers_on(D(2023, 1, 4)) == frozenset()


def test_last_traded_closes_an_era_and_leaves_a_real_gap() -> None:
    facts = [
        fact(STARTED, "ABC", D(2010, 1, 4)),
        fact(LAST, "ABC", D(2015, 3, 31)),
        fact(STARTED, "XYZ", D(2015, 6, 1)),
    ]
    history = derive_security_history(SECURITY, facts, [listing("XYZ", D(2026, 1, 1))])

    assert history.sourced_tickers_on(D(2015, 3, 31)) == {"ABC"}, "last traded date is inclusive"
    assert history.sourced_tickers_on(D(2015, 4, 1)) == frozenset(), "the gap is not filled"
    assert history.sourced_tickers_on(D(2015, 5, 31)) == frozenset()
    assert history.sourced_tickers_on(D(2015, 6, 1)) == {"XYZ"}


def test_two_observations_never_become_a_continuous_interval() -> None:
    facts = [fact(OBSERVED, "AAPL", D(2020, 1, 2)), fact(OBSERVED, "AAPL", D(2020, 6, 1))]
    history = derive_security_history(SECURITY, facts, [])

    assert history.sourced_tickers_on(D(2020, 1, 2)) == {"AAPL"}
    assert history.sourced_tickers_on(D(2020, 6, 1)) == {"AAPL"}
    assert history.sourced_tickers_on(D(2020, 3, 1)) == frozenset(), "nothing between two snapshots"
    assert history.covered_ranges("AAPL", D(2020, 1, 1), D(2020, 12, 31)) == [
        (D(2020, 1, 2), D(2020, 1, 2)),
        (D(2020, 6, 1), D(2020, 6, 1)),
    ]


def test_an_observation_that_contradicts_the_ticker_events_is_a_conflict() -> None:
    facts = [*FB_META, fact(OBSERVED, "META", D(2018, 3, 1))]
    history = derive_security_history(SECURITY, facts, [])

    assert history.conflict_on(D(2018, 3, 1)) is not None
    assert history.conflict_on(D(2018, 3, 2)) is None


def test_an_observation_inside_an_era_of_the_same_ticker_adds_nothing() -> None:
    facts = [*FB_META, fact(OBSERVED, "FB", D(2018, 3, 1))]
    history = derive_security_history(SECURITY, facts, [listing("META", D(2026, 1, 1))])

    assert history.conflicts == ()
    assert len(history.sourced_eras) == 2


def test_one_source_starting_two_tickers_on_one_date_is_a_conflict() -> None:
    facts = [fact(STARTED, "AAA", D(2020, 1, 2)), fact(STARTED, "BBB", D(2020, 1, 2))]
    history = derive_security_history(SECURITY, facts, [])

    assert history.conflict_on(D(2020, 1, 2)) is not None


def test_sources_that_agree_merge_and_do_not_conflict() -> None:
    facts = [*FB_META, *[fact(f.kind, f.ticker, f.effective_date, OTHER) for f in FB_META]]
    history = derive_security_history(SECURITY, facts, [listing("META", D(2026, 1, 1))])

    assert history.conflicts == ()
    assert history.sourced_tickers_on(D(2015, 1, 1)) == {"FB"}


def test_sources_that_disagree_on_a_ticker_are_a_conflict_with_no_precedence() -> None:
    facts = [
        fact(STARTED, "AAA", D(2020, 1, 2), MASSIVE),
        fact(STARTED, "BBB", D(2020, 1, 2), OTHER),
    ]
    history = derive_security_history(SECURITY, facts, [])

    conflict = history.conflict_on(D(2020, 1, 2))
    assert conflict is not None and "disagree" in conflict.reason


def test_sources_that_disagree_on_a_start_date_conflict_over_the_disputed_dates_only() -> None:
    facts = [
        fact(STARTED, "AAA", D(2020, 1, 2), MASSIVE),
        fact(STARTED, "AAA", D(2020, 2, 3), OTHER),
    ]
    history = derive_security_history(SECURITY, facts, [listing("AAA", D(2026, 1, 1))])

    assert history.conflict_on(D(2020, 1, 15)) is not None
    assert history.conflict_on(D(2020, 2, 3)) is None
    assert history.conflict_on(D(2020, 1, 1)) is None


def test_a_first_observed_listing_contradicting_source_history_is_a_conflict() -> None:
    facts = [fact(STARTED, "OLD", D(2010, 1, 4)), fact(STARTED, "NEW", D(2024, 1, 2))]
    history = derive_security_history(SECURITY, facts, [listing("OLD", D(2026, 1, 1))])

    assert history.conflict_on(D(2026, 6, 1)) is not None


def test_a_listing_that_matches_the_latest_source_era_is_consistent() -> None:
    history = derive_security_history(SECURITY, FB_META, [listing("META", D(2026, 10, 2))])
    assert history.conflicts == ()


def test_covered_ranges_union_both_evidence_kinds_and_clamp_to_the_query() -> None:
    history = derive_security_history(
        SECURITY, [fact(STARTED, "AAPL", D(2000, 1, 3))], [listing("AAPL", D(2026, 1, 1))]
    )

    assert history.covered_ranges("AAPL", D(2020, 1, 1), D(2020, 12, 31)) == [
        (D(2020, 1, 1), D(2020, 12, 31))
    ]
    assert history.covered_ranges("AAPL", D(1999, 1, 1), D(2001, 1, 1)) == [
        (D(2000, 1, 3), D(2001, 1, 1))
    ]
    assert history.covered_ranges("ZZZ", D(2020, 1, 1), D(2020, 12, 31)) == []


def test_the_derivation_is_independent_of_input_order() -> None:
    facts = [
        *FB_META,
        fact(OBSERVED, "FB", D(2018, 3, 1)),
        fact(LAST, "FB", D(2022, 6, 8)),
        fact(STARTED, "META", D(2022, 6, 9), OTHER),
        fact(OBSERVED, "META", D(2024, 1, 2)),
    ]
    listings = [listing("META", D(2026, 10, 2))]
    reference = derive_security_history(SECURITY, facts, listings)
    rng = random.Random(7)
    for _ in range(50):
        shuffled = facts[:]
        rng.shuffle(shuffled)
        again = derive_security_history(SECURITY, shuffled, listings)
        assert again.sourced_eras == reference.sourced_eras
        assert again.conflicts == reference.conflicts


def test_evidence_is_labelled_by_kind() -> None:
    history = derive_security_history(SECURITY, FB_META, [listing("META", D(2026, 10, 2))])

    assert {era.evidence for era in history.sourced_eras} == {Evidence.SOURCE_STATED_HISTORY}
    assert {era.evidence for era in history.provisional_eras} == {
        Evidence.PROVISIONAL_FIRST_OBSERVED
    }


@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_the_exclusive_end_of_a_provisional_listing_is_respected(offset: int) -> None:
    ended = D(2024, 3, 1)
    history = derive_security_history(SECURITY, [], [listing("T", D(2010, 1, 1), ended)])
    on = ended + timedelta(days=offset)

    assert (history.provisional_tickers_on(on) == {"T"}) is (offset < 0)


# ── review fixes ─────────────────────────────────────────────────────────────


def test_an_observation_inside_another_sources_era_corroborates_and_never_conflicts() -> None:
    facts = [
        fact(STARTED, "FB", D(2012, 5, 18), MASSIVE),
        fact(LAST, "FB", D(2022, 6, 8), MASSIVE),
        fact(OBSERVED, "FB", D(2015, 3, 2), OTHER),
    ]
    history = derive_security_history(SECURITY, facts, [])

    assert history.conflicts == ()
    assert history.sourced_tickers_on(D(2018, 1, 1)) == {"FB"}, "the long era is not disturbed"


def test_an_observation_of_a_different_ticker_inside_another_sources_era_is_a_conflict() -> None:
    facts = [
        fact(STARTED, "FB", D(2012, 5, 18), MASSIVE),
        fact(LAST, "FB", D(2022, 6, 8), MASSIVE),
        fact(OBSERVED, "ZZZ", D(2015, 3, 2), OTHER),
    ]
    history = derive_security_history(SECURITY, facts, [])

    assert history.conflict_on(D(2015, 3, 2)) is not None
    assert history.conflict_on(D(2015, 3, 3)) is None


def test_an_observation_after_a_stated_last_traded_date_is_a_conflict_not_a_new_era() -> None:
    facts = [
        fact(STARTED, "ABC", D(2010, 1, 1)),
        fact(LAST, "ABC", D(2015, 3, 31)),
        fact(STARTED, "XYZ", D(2015, 6, 1)),
        fact(OBSERVED, "ABC", D(2015, 5, 1)),
    ]
    history = derive_security_history(SECURITY, facts, [listing("XYZ", D(2026, 1, 1))])

    assert history.conflict_on(D(2015, 5, 1)) is not None
    assert history.sourced_tickers_on(D(2015, 5, 1)) == frozenset(), "no stray ABC era"


def test_a_relisting_under_the_same_ticker_after_last_traded_is_not_a_conflict() -> None:
    facts = [
        fact(STARTED, "ABC", D(2010, 1, 1)),
        fact(LAST, "ABC", D(2015, 3, 31)),
        fact(STARTED, "ABC", D(2016, 1, 4)),
        fact(OBSERVED, "ABC", D(2017, 1, 3)),
    ]
    history = derive_security_history(SECURITY, facts, [listing("ABC", D(2026, 1, 1))])

    assert history.conflicts == ()
    assert history.sourced_tickers_on(D(2015, 7, 1)) == frozenset()
    assert history.sourced_tickers_on(D(2017, 1, 3)) == {"ABC"}


def test_a_closed_last_era_does_not_contradict_a_later_listing_under_another_ticker() -> None:
    facts = [fact(STARTED, "OLD", D(2010, 1, 4)), fact(LAST, "OLD", D(2020, 1, 31))]
    history = derive_security_history(SECURITY, facts, [listing("NEW", D(2026, 1, 1))])

    assert history.conflicts == ()


def test_a_conflict_concerns_only_the_ticker_whose_era_it_overlaps() -> None:
    facts = [
        fact(STARTED, "AAA", D(2020, 1, 2), MASSIVE),
        fact(STARTED, "BBB", D(2020, 1, 2), OTHER),
    ]
    history = derive_security_history(SECURITY, facts, [])

    assert history.conflict_for_ticker_on("AAA", D(2020, 1, 2)) is not None
    assert history.conflict_for_ticker_on("QQQ", D(2020, 1, 2)) is None


def test_a_source_that_merely_knows_less_does_not_conflict_with_one_that_states_an_end() -> None:
    facts = [
        fact(STARTED, "X", D(2010, 1, 1), MASSIVE),
        fact(STARTED, "X", D(2010, 1, 1), OTHER),
        fact(LAST, "X", D(2015, 1, 1), OTHER),
    ]
    history = derive_security_history(SECURITY, facts, [])

    assert history.conflicts == ()


def test_sources_that_both_state_different_ends_do_conflict() -> None:
    facts = [
        fact(STARTED, "X", D(2010, 1, 1), MASSIVE),
        fact(LAST, "X", D(2014, 1, 1), MASSIVE),
        fact(STARTED, "X", D(2010, 1, 1), OTHER),
        fact(LAST, "X", D(2015, 1, 1), OTHER),
    ]
    history = derive_security_history(SECURITY, facts, [])

    assert history.conflict_on(D(2014, 6, 1)) is not None


def test_an_earlier_ticker_observed_after_an_unclosed_later_start_is_a_conflict() -> None:
    facts = [
        fact(STARTED, "A", D(2010, 1, 1)),
        fact(STARTED, "B", D(2020, 1, 1)),
        fact(OBSERVED, "A", D(2021, 1, 1)),
    ]
    history = derive_security_history(SECURITY, facts, [])

    assert history.conflict_on(D(2021, 1, 1)) is not None
    assert history.conflict_for_ticker_on("A", D(2021, 1, 1)) is not None, (
        "A's evidence is the disputed one"
    )


def test_an_observation_after_another_sources_last_traded_is_a_conflict() -> None:
    facts = [
        fact(STARTED, "X", D(2010, 1, 1), MASSIVE),
        fact(LAST, "X", D(2015, 1, 1), MASSIVE),
        fact(OBSERVED, "X", D(2020, 1, 1), OTHER),
    ]
    history = derive_security_history(SECURITY, facts, [])

    assert history.conflict_on(D(2020, 1, 1)) is not None
    assert history.conflict_for_ticker_on("X", D(2020, 1, 1)) is not None


def test_a_tail_ended_by_another_source_is_not_open_so_it_contradicts_nothing() -> None:
    facts = [
        fact(STARTED, "ABC", D(2015, 1, 1), MASSIVE),
        fact(LAST, "ABC", D(2018, 1, 1), OTHER),
        fact(OBSERVED, "XYZ", D(2020, 1, 1), MASSIVE),
    ]
    history = derive_security_history(SECURITY, facts, [])

    assert history.conflicts == ()


def test_an_observation_from_a_source_with_no_chain_meets_another_sources_open_tail() -> None:
    facts = [fact(STARTED, "B", D(2020, 1, 1), MASSIVE), fact(OBSERVED, "A", D(2021, 1, 1), OTHER)]
    history = derive_security_history(SECURITY, facts, [])

    assert history.conflict_on(D(2021, 1, 1)) is not None


def test_two_observed_tickers_on_one_date_are_a_symmetric_conflict() -> None:
    facts = [fact(OBSERVED, "AAA", D(2024, 1, 2)), fact(OBSERVED, "BBB", D(2024, 1, 2))]
    history = derive_security_history(SECURITY, facts, [])

    assert history.sourced_tickers_on(D(2024, 1, 2)) == frozenset(), "neither becomes an era"
    assert history.conflict_for_ticker_on("AAA", D(2024, 1, 2)) is not None
    assert history.conflict_for_ticker_on("BBB", D(2024, 1, 2)) is not None


def test_a_second_covering_conflict_about_the_ticker_is_not_hidden_by_the_first() -> None:
    facts = [
        fact(STARTED, "X", D(2020, 1, 2), MASSIVE),
        fact(STARTED, "Y", D(2020, 1, 2), OTHER),
        fact(OBSERVED, "SHR", D(2020, 1, 2), MASSIVE),
    ]
    history = derive_security_history(SECURITY, facts, [])

    conflict = history.conflict_for_ticker_on("SHR", D(2020, 1, 2))
    assert conflict is not None and "SHR" in conflict.tickers


def test_a_second_sources_unclosed_era_is_not_stretched_past_anothers_stated_last_traded() -> None:
    facts = [
        fact(STARTED, "A", D(2020, 1, 1), MASSIVE),
        fact(LAST, "A", D(2020, 1, 5), MASSIVE),
        fact(STARTED, "A", D(2020, 1, 1), OTHER),
        fact(OBSERVED, "A", D(2020, 1, 7), OTHER),
    ]
    history = derive_security_history(SECURITY, facts, [])

    assert history.sourced_tickers_on(D(2020, 1, 5)) == {"A"}
    assert history.sourced_tickers_on(D(2020, 1, 6)) == frozenset(), "the stated end holds"
    assert history.conflict_on(D(2020, 1, 7)) is not None, "the later observation contradicts it"


def test_a_corroborating_open_listing_does_not_stretch_an_era_past_a_stated_end() -> None:
    facts = [
        fact(STARTED, "A", D(2020, 1, 1), MASSIVE),
        fact(STARTED, "A", D(2020, 1, 1), OTHER),
        fact(LAST, "A", D(2020, 1, 5), OTHER),
    ]
    history = derive_security_history(SECURITY, facts, [listing("A", D(2026, 1, 1))])

    assert history.sourced_tickers_on(D(2020, 6, 1)) == frozenset()
