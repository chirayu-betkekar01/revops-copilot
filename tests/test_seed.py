"""Tests for the seed script's checks and conversions, using a tiny hand-made dataset."""

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

from seed.models import Company, Deal, Note, SeedData
from seed.seed_hubspot import (
    DEAL_TO_PRIMARY_COMPANY,
    deal_input,
    ref_to_id,
    rep_dropdown,
    to_hubspot_datetime,
    validate,
)

COMPANY = Company("co_1", "Acme", "acme.example", "RETAIL", 50, Decimal("1000000"))
DEAL = Deal(
    "deal_1",
    "co_1",
    "rep_01",
    "Acme renewal",
    Decimal("12500.50"),
    "closedwon",
    date(2026, 1, 5),
    date(2026, 3, 31),
)
NOTE = Note("deal_1", "Call went well", datetime(2026, 2, 1, 15, 0, tzinfo=UTC))


def dataset(**overrides: list[object]) -> SeedData:
    base: dict[str, list[object]] = {"companies": [COMPANY], "deals": [DEAL], "notes": [NOTE]}
    base.update(overrides)
    return SeedData(**base)  # type: ignore[arg-type]


def test_valid_dataset_has_no_problems() -> None:
    assert validate(dataset()) == []


def test_catches_deal_pointing_at_missing_company() -> None:
    problems = validate(dataset(deals=[replace(DEAL, company_ref="co_404")]))
    assert any("co_404" in p for p in problems)


def test_catches_unknown_stage_and_rep() -> None:
    problems = validate(dataset(deals=[replace(DEAL, stage="maybe", rep_id="rep_99")]))
    assert len(problems) == 2


def test_catches_duplicate_refs() -> None:
    problems = validate(dataset(companies=[COMPANY, COMPANY]))
    assert any("Duplicate company" in p for p in problems)


def test_catches_note_without_timezone() -> None:
    problems = validate(dataset(notes=[replace(NOTE, timestamp=datetime(2026, 2, 1))]))
    assert any("timezone" in p for p in problems)


def test_deal_input_keeps_exact_amount_and_links_company() -> None:
    payload = deal_input(DEAL, company_id="999")

    assert payload["properties"]["amount"] == "12500.50"
    assert payload["properties"]["closedate"] == "2026-03-31T00:00:00Z"
    assoc = payload["associations"][0]
    assert assoc["to"]["id"] == "999"
    assert assoc["types"][0]["associationTypeId"] == DEAL_TO_PRIMARY_COMPANY


def test_rep_dropdown_stores_id_and_shows_name() -> None:
    prop = rep_dropdown()

    assert prop["type"] == "enumeration"
    assert {"label": "Priya Shah", "value": "rep_01", "displayOrder": 0} in prop["options"]
    assert len(prop["options"]) == 8


def test_to_hubspot_datetime_converts_to_utc() -> None:
    assert to_hubspot_datetime(date(2026, 1, 1)) == "2026-01-01T00:00:00Z"


def test_ref_to_id_maps_by_ref_not_position() -> None:
    results = [
        {"id": "b-id", "properties": {"seed_ref": "b"}},
        {"id": "a-id", "properties": {"seed_ref": "a"}},
    ]
    assert ref_to_id(results, expected=2) == {"a": "a-id", "b": "b-id"}
