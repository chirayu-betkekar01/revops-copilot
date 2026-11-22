"""Push synthetic companies, deals and notes into a HubSpot developer test account.

    just seed-dry   # generate + check the data, save it to seed/output/, touch nothing in HubSpot
    just seed       # check HubSpot, ask for confirmation, then create everything

The fake data comes from seed/generator.py (hand-written). This file only checks it and sends it.
"""

import argparse
import json
import random
from collections import Counter
from dataclasses import asdict
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

from seed.generator import generate
from seed.hubspot_client import JSON, HubSpotClient
from seed.models import (
    ALL_STAGES,
    INDUSTRIES,
    LOST_STAGE,
    REP_IDS,
    REPS,
    SEED_BATCH,
    WON_STAGE,
    Company,
    Deal,
    Note,
    SeedData,
)

OUTPUT_PATH = Path("seed/output/seed_data.json")
PIPELINE_ID = "default"

# HubSpot's built-in association type IDs (category HUBSPOT_DEFINED).
DEAL_TO_PRIMARY_COMPANY = 5
NOTE_TO_DEAL = 214


def text_property(name: str, label: str, group: str) -> JSON:
    return {"name": name, "label": label, "type": "string", "fieldType": "text", "groupName": group}


def rep_dropdown() -> JSON:
    """`rep_id` as a dropdown: HubSpot stores the id (rep_07) and shows the name (Sofia Rossi)."""
    return {
        "name": "rep_id",
        "label": "Sales rep",
        "type": "enumeration",
        "fieldType": "select",
        "groupName": "dealinformation",
        "options": [
            {"label": rep.name, "value": rep.id, "displayOrder": i} for i, rep in enumerate(REPS)
        ],
    }


# Custom properties this script needs. Created if missing, left alone if they already exist.
CUSTOM_PROPERTIES: dict[str, list[JSON]] = {
    "companies": [
        text_property("seed_batch", "Seed batch", "companyinformation"),
        text_property("seed_ref", "Seed ref", "companyinformation"),
    ],
    "deals": [
        rep_dropdown(),
        text_property("seed_batch", "Seed batch", "dealinformation"),
        text_property("seed_ref", "Seed ref", "dealinformation"),
    ],
}

# Built-in properties we write. Checked before sending, so a typo fails fast, not mid-upload.
BUILT_IN_PROPERTIES = {
    "companies": {"name", "domain", "industry", "numberofemployees", "annualrevenue"},
    "deals": {
        "dealname",
        "amount",
        "dealstage",
        "pipeline",
        "createdate",
        "closedate",
        "hs_next_step",
    },
}


# ---------- 1. Check the generated data (no network) ----------


def validate(data: SeedData) -> list[str]:
    """Return a list of problems with the generated data. Empty list = good to send."""
    problems: list[str] = []

    company_refs = [c.ref for c in data.companies]
    deal_refs = [d.ref for d in data.deals]
    for kind, refs in (("company", company_refs), ("deal", deal_refs)):
        dupes = [ref for ref, n in Counter(refs).items() if n > 1]
        if dupes:
            problems.append(f"Duplicate {kind} refs: {dupes[:5]}")

    known_companies = set(company_refs)
    for c in data.companies:
        if c.industry not in INDUSTRIES:
            problems.append(f"Company {c.ref}: unknown industry {c.industry!r}")
    for d in data.deals:
        if d.company_ref not in known_companies:
            problems.append(f"Deal {d.ref}: company_ref {d.company_ref!r} doesn't exist")
        if d.rep_id not in REP_IDS:
            problems.append(f"Deal {d.ref}: unknown rep_id {d.rep_id!r}")
        if d.stage not in ALL_STAGES:
            problems.append(f"Deal {d.ref}: unknown stage {d.stage!r}")
        if d.close_date < d.create_date:
            problems.append(f"Deal {d.ref}: closes before it was created")

    known_deals = set(deal_refs)
    for n in data.notes:
        if n.deal_ref not in known_deals:
            problems.append(f"Note on {n.deal_ref!r}: deal doesn't exist")
        if n.timestamp.tzinfo is None:
            problems.append(f"Note on {n.deal_ref!r}: timestamp has no timezone")

    return problems


def summarize(data: SeedData) -> str:
    stages = Counter(d.stage for d in data.deals)
    won, lost = stages[WON_STAGE], stages[LOST_STAGE]
    closed = won + lost
    win_rate = f"{won / closed:.0%}" if closed else "n/a"
    still_open = len(data.deals) - closed
    reps = Counter(d.rep_id for d in data.deals)
    return "\n".join(
        [
            f"Companies: {len(data.companies)}",
            f"Deals:     {len(data.deals)}  (won {won}, lost {lost}, open {still_open})",
            f"Win rate:  {win_rate} of closed deals",
            f"Notes:     {len(data.notes)}",
            "Deals per rep: " + ", ".join(f"{r.name} {reps[r.id]}" for r in REPS),
        ]
    )


def write_dry_run(data: SeedData) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(asdict(data), indent=2, default=str), encoding="utf-8")


# ---------- 2. Turn dataclasses into HubSpot API inputs ----------


def to_hubspot_datetime(value: date | datetime) -> str:
    """HubSpot accepts ISO 8601 in UTC. Plain dates become midnight UTC."""
    if not isinstance(value, datetime):
        value = datetime.combine(value, time.min, tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def association(to_id: str, type_id: int) -> JSON:
    return {
        "to": {"id": to_id},
        "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": type_id}],
    }


def company_input(c: Company) -> JSON:
    return {
        "properties": {
            "name": c.name,
            "domain": c.domain,
            "industry": c.industry,
            "numberofemployees": str(c.number_of_employees),
            "annualrevenue": str(c.annual_revenue),
            "seed_batch": SEED_BATCH,
            "seed_ref": c.ref,
        }
    }


def deal_input(d: Deal, company_id: str) -> JSON:
    properties: dict[str, Any] = {
        "dealname": d.name,
        "amount": str(d.amount),  # str(Decimal) keeps exact cents; float could round
        "dealstage": d.stage,
        "pipeline": PIPELINE_ID,
        "createdate": to_hubspot_datetime(d.create_date),
        "closedate": to_hubspot_datetime(d.close_date),
        "rep_id": d.rep_id,
        "seed_batch": SEED_BATCH,
        "seed_ref": d.ref,
    }
    if d.next_step:
        properties["hs_next_step"] = d.next_step
    return {
        "properties": properties,
        "associations": [association(company_id, DEAL_TO_PRIMARY_COMPANY)],
    }


def note_input(n: Note, deal_id: str) -> JSON:
    return {
        "properties": {"hs_note_body": n.body, "hs_timestamp": to_hubspot_datetime(n.timestamp)},
        "associations": [association(deal_id, NOTE_TO_DEAL)],
    }


def ref_to_id(results: list[JSON], expected: int) -> dict[str, str]:
    """Map our seed_ref -> HubSpot's new record id. Uses the ref, not list position,
    because HubSpot doesn't promise results come back in the order we sent them."""
    mapping = {r["properties"]["seed_ref"]: r["id"] for r in results}
    if len(mapping) != expected:
        raise RuntimeError(f"Expected {expected} created records, got {len(mapping)}")
    return mapping


# ---------- 3. Talk to HubSpot ----------


def preflight(hs: HubSpotClient) -> None:
    """Check the account has everything we'll write to, before creating a single record."""
    pipelines = hs.get("/crm/v3/pipelines/deals")["results"]
    default = next((p for p in pipelines if p["id"] == PIPELINE_ID), None)
    if default is None:
        raise SystemExit(f"No deal pipeline with id {PIPELINE_ID!r} in this account.")
    missing_stages = set(ALL_STAGES) - {s["id"] for s in default["stages"]}
    if missing_stages:
        raise SystemExit(f"Default pipeline is missing stages: {sorted(missing_stages)}")

    for object_type, wanted in BUILT_IN_PROPERTIES.items():
        existing = {p["name"] for p in hs.get(f"/crm/v3/properties/{object_type}")["results"]}
        missing = wanted - existing
        if missing:
            raise SystemExit(f"{object_type} is missing properties: {sorted(missing)}")

    industry = hs.get("/crm/v3/properties/companies/industry")
    allowed = {o["value"] for o in industry["options"]}
    bad = set(INDUSTRIES) - allowed
    if bad:
        raise SystemExit(f"HubSpot doesn't accept these industry values: {sorted(bad)}")


def ensure_custom_properties(hs: HubSpotClient) -> None:
    for object_type, definitions in CUSTOM_PROPERTIES.items():
        for definition in definitions:
            created = hs.create_property(object_type, definition)
            state = "created" if created else "already exists"
            print(f"  property {object_type}.{definition['name']}: {state}")


def check_write_permissions(hs: HubSpotClient) -> None:
    """Prove the key can create every record type BEFORE the upload starts, so a missing
    scope can't leave a half-finished upload behind (e.g. deals created, notes refused)."""
    now = to_hubspot_datetime(datetime.now(UTC))
    probes: dict[str, JSON] = {
        "companies": {"name": "Seed permission check (deleted)"},
        "deals": {
            "dealname": "Seed permission check (deleted)",
            "pipeline": PIPELINE_ID,
            "dealstage": "appointmentscheduled",
        },
        "notes": {"hs_note_body": "Seed permission check (deleted)", "hs_timestamp": now},
    }
    for object_type, properties in probes.items():
        hs.check_can_write(object_type, properties)
        print(f"  can create {object_type}: yes")


def guard_against_duplicates(hs: HubSpotClient) -> None:
    for object_type in ("companies", "deals"):
        n = hs.count(object_type, "seed_batch", SEED_BATCH)
        if n:
            raise SystemExit(
                f"{n} {object_type} with seed_batch={SEED_BATCH!r} already exist. "
                "Running again would create duplicates. Delete them first."
            )


def push(hs: HubSpotClient, data: SeedData) -> None:
    print("Companies...")
    results = hs.batch_create("companies", [company_input(c) for c in data.companies])
    company_ids = ref_to_id(results, len(data.companies))

    print("Deals...")
    deal_inputs = [deal_input(d, company_ids[d.company_ref]) for d in data.deals]
    deal_ids = ref_to_id(hs.batch_create("deals", deal_inputs), len(data.deals))

    print("Notes...")
    hs.batch_create("notes", [note_input(n, deal_ids[n.deal_ref]) for n in data.notes])


# ---------- Entry point ----------


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--dry-run", action="store_true", help="generate and check only")
    parser.add_argument("--seed", type=int, default=42, help="random seed (same seed = same data)")
    args = parser.parse_args(argv)

    data = generate(random.Random(args.seed))
    problems = validate(data)
    if problems:
        print(f"Generated data has {len(problems)} problem(s):")
        for p in problems[:20]:
            print(f"  - {p}")
        raise SystemExit(1)
    print(summarize(data))

    if args.dry_run:
        write_dry_run(data)
        print(f"\nDry run: nothing sent. Data saved to {OUTPUT_PATH}")
        return

    with HubSpotClient.from_env() as hs:
        print("\nChecking HubSpot account...")
        preflight(hs)
        ensure_custom_properties(hs)
        check_write_permissions(hs)
        guard_against_duplicates(hs)

        total = len(data.companies) + len(data.deals) + len(data.notes)
        if input(f"\nCreate {total} records in HubSpot? [y/N] ").strip().lower() != "y":
            print("Cancelled. Nothing created.")
            return
        push(hs, data)
    print("Done.")


if __name__ == "__main__":
    main()
