"""Checks that the synthetic data has the shape docs/SCOPE.md asks for."""

import random
from collections import Counter

import pytest

from seed.generator import AS_OF, EXPANSION_OPENING_NOTES, generate
from seed.models import LOST_STAGE, OPEN_STAGES, REPS, WON_STAGE, Note, SeedData
from seed.seed_hubspot import validate


@pytest.fixture(scope="module")
def data() -> SeedData:
    return generate(random.Random(42))


def test_passes_validation(data: SeedData) -> None:
    assert validate(data) == []


def test_counts_match_scope(data: SeedData) -> None:
    assert len(data.companies) == 300
    assert len(data.deals) == 2000
    assert all(1 <= n <= 5 for n in Counter(note.deal_ref for note in data.notes).values())


def test_win_rate_between_20_and_30_percent(data: SeedData) -> None:
    stages = Counter(d.stage for d in data.deals)
    win_rate = stages[WON_STAGE] / (stages[WON_STAGE] + stages[LOST_STAGE])
    assert 0.20 <= win_rate <= 0.30


def test_no_deal_created_in_the_future(data: SeedData) -> None:
    assert all(d.create_date <= AS_OF for d in data.deals)


def test_has_open_pipeline_and_some_overdue_deals(data: SeedData) -> None:
    open_deals = [d for d in data.deals if d.stage in OPEN_STAGES]
    assert len(open_deals) > 100
    assert any(d.close_date < AS_OF for d in open_deals)  # stale CRM data, on purpose


def test_closing_note_is_last_and_on_close_date(data: SeedData) -> None:
    notes_by_deal: dict[str, list[Note]] = {}
    for note in data.notes:
        notes_by_deal.setdefault(note.deal_ref, []).append(note)

    for deal in data.deals:
        if deal.stage in (WON_STAGE, LOST_STAGE):
            notes = notes_by_deal[deal.ref]
            last = max(notes, key=lambda n: n.timestamp)
            assert last.timestamp.date() == deal.close_date
            assert all(n.timestamp.date() <= deal.close_date for n in notes)


def test_expansion_deals_open_with_existing_customer_note(data: SeedData) -> None:
    first_note: dict[str, str] = {}
    for note in sorted(data.notes, key=lambda n: n.timestamp):
        first_note.setdefault(note.deal_ref, note.body)

    for deal in data.deals:
        if deal.name.endswith("(Expansion)"):
            assert first_note[deal.ref] in EXPANSION_OPENING_NOTES


def test_rep_names_are_unique() -> None:
    assert len({rep.name for rep in REPS}) == len(REPS)
    assert len({rep.id for rep in REPS}) == len(REPS)


def test_same_seed_gives_same_data() -> None:
    assert generate(random.Random(7)) == generate(random.Random(7))
