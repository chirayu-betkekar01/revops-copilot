"""Archive one batch of seeded data in HubSpot (companies, deals and their notes).

    just seed-archive seed-v1

Archived records go to HubSpot's recycle bin and can be restored for about 90 days, so
this is the least destructive way to clear seeded data. Only records tagged with the given
seed_batch (and the notes linked to those deals) are touched. Asks before archiving.
"""

import argparse

from seed.hubspot_client import HubSpotClient


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Archive one seed batch in HubSpot")
    parser.add_argument("batch", help="seed_batch value to archive, e.g. seed-v1")
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = parser.parse_args(argv)

    with HubSpotClient.from_env() as hs:
        print(f"Finding records with seed_batch={args.batch!r}...")
        deal_ids = hs.search_ids("deals", "seed_batch", args.batch)
        company_ids = hs.search_ids("companies", "seed_batch", args.batch)
        note_ids = hs.associated_ids("deals", "notes", deal_ids)
        print(f"  {len(company_ids)} companies, {len(deal_ids)} deals, {len(note_ids)} notes")

        total = len(company_ids) + len(deal_ids) + len(note_ids)
        if not total:
            print("Nothing to archive.")
            return
        if not args.yes:
            answer = input(f"Archive these {total} records (restorable for ~90 days)? [y/N] ")
            if answer.strip().lower() != "y":
                print("Cancelled. Nothing archived.")
                return

        # Notes first, then deals, then companies: children before the records they hang off.
        for object_type, ids in (
            ("notes", note_ids),
            ("deals", deal_ids),
            ("companies", company_ids),
        ):
            hs.batch_archive(object_type, ids)
            print(f"  archived {len(ids)} {object_type}")
    print("Done.")


if __name__ == "__main__":
    main()
