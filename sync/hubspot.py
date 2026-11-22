"""Full sync: copy HubSpot companies, deals, notes and deal pipelines into raw.* tables.

    just sync

Every run pages through every record and upserts it, so running it twice (or re-running
after a crash) never creates duplicates. Each run is logged in ops.sync_runs.
"""

import argparse
import os
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import LiteralString
from urllib.parse import urlencode

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from seed.hubspot_client import JSON, HubSpotClient

PAGE_SIZE = 100  # HubSpot's max for list endpoints
UPSERT_SQL_PATH = Path("sql/sync/upsert_raw.sql")


@dataclass(frozen=True)
class SyncObject:
    object_type: str  # HubSpot's name, used in the URL
    table: str  # our raw.* table
    # List endpoints only return a few default properties unless you name the ones you want.
    properties: tuple[str, ...]
    # Linked records to include in each payload, e.g. a deal's company.
    associations: tuple[str, ...] = ()


OBJECTS = (
    SyncObject(
        "companies",
        "hubspot_companies",
        ("name", "domain", "industry", "numberofemployees", "annualrevenue")
        + ("createdate", "hs_lastmodifieddate", "seed_batch", "seed_ref"),
    ),
    SyncObject(
        "deals",
        "hubspot_deals",
        ("dealname", "amount", "dealstage", "pipeline", "closedate", "createdate")
        + ("hs_lastmodifieddate", "hs_is_closed_won", "hs_next_step", "rep_id")
        + ("seed_batch", "seed_ref"),
        associations=("companies",),
    ),
    SyncObject(
        "notes",
        "hubspot_notes",
        ("hs_note_body", "hs_timestamp", "hs_lastmodifieddate"),
        associations=("deals",),
    ),
)

# Property definitions to copy: (object type, property name). The rep_id dropdown's options
# are where rep names live, so HubSpot stays the one source of truth for them.
PROPERTY_DEFINITIONS = (("deals", "rep_id"),)


@dataclass(frozen=True)
class SyncResult:
    run_id: int
    counts: dict[str, int]


# ---------- Reading from HubSpot ----------


def iter_pages(hs: HubSpotClient, obj: SyncObject) -> Iterator[list[JSON]]:
    """Yield one page (up to 100 records) at a time, following HubSpot's cursor."""
    after: str | None = None
    while True:
        params = {"limit": PAGE_SIZE, "properties": ",".join(obj.properties)}
        if obj.associations:
            params["associations"] = ",".join(obj.associations)
        if after:
            params["after"] = after  # the bookmark from the previous page
        body = hs.get(f"/crm/v3/objects/{obj.object_type}?{urlencode(params)}")
        yield body["results"]

        after = body.get("paging", {}).get("next", {}).get("after")
        if not after:  # no bookmark = that was the last page
            return


# ---------- Writing to Postgres ----------


def load_upsert_sql(path: Path = UPSERT_SQL_PATH) -> LiteralString:
    """Read the hand-written upsert statement, dropping `--` comment lines."""
    lines = path.read_text(encoding="utf-8").splitlines()
    statement = "\n".join(line for line in lines if not line.lstrip().startswith("--")).strip()
    if not statement:
        raise SystemExit(f"{path} has no SQL yet. Write the upsert statement first.")
    # The SQL comes from a reviewed file in this repo, never from user input or HubSpot data,
    # so it's safe to treat as a literal query (LiteralString). Values still travel separately
    # as parameters, so HubSpot data can never change what the SQL does.
    return statement


def upsert_page(
    conn: psycopg.Connection, upsert: LiteralString, table: str, records: Sequence[JSON]
) -> int:
    """Save one page in its own transaction: a crash later keeps every page saved so far."""
    query = sql.SQL(upsert).format(table=sql.Identifier("raw", table))  # quotes it safely
    rows = [{"hubspot_id": str(r["id"]), "payload": Jsonb(r)} for r in records]
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(query, rows)
    return len(rows)


def start_run(conn: psycopg.Connection) -> int:
    row = conn.execute(
        "INSERT INTO ops.sync_runs (status) VALUES ('running') RETURNING run_id"
    ).fetchone()
    assert row is not None
    return int(row[0])


def finish_run(
    conn: psycopg.Connection,
    run_id: int,
    status: str,
    counts: dict[str, int],
    error: str | None = None,
) -> None:
    conn.execute(
        "UPDATE ops.sync_runs SET status = %s, finished_at = now(), row_counts = %s, error = %s"
        " WHERE run_id = %s",
        (status, Jsonb(counts), error, run_id),
    )


# ---------- The sync ----------


def run_sync(
    conn: psycopg.Connection,
    hs: HubSpotClient,
    objects: Sequence[SyncObject] = OBJECTS,
    property_definitions: Sequence[tuple[str, str]] = PROPERTY_DEFINITIONS,
) -> SyncResult:
    upsert = load_upsert_sql()
    run_id = start_run(conn)
    counts: dict[str, int] = {}
    try:
        pipelines = hs.get("/crm/v3/pipelines/deals")["results"]  # small: no paging needed
        counts["pipelines"] = upsert_page(conn, upsert, "hubspot_pipelines", pipelines)

        definitions = [
            {"id": f"{object_type}.{name}", **hs.get(f"/crm/v3/properties/{object_type}/{name}")}
            for object_type, name in property_definitions
        ]
        counts["properties"] = upsert_page(conn, upsert, "hubspot_properties", definitions)

        for obj in objects:
            counts[obj.object_type] = 0
            for page in iter_pages(hs, obj):
                counts[obj.object_type] += upsert_page(conn, upsert, obj.table, page)
                print(f"  {obj.object_type}: {counts[obj.object_type]} saved", flush=True)
    except BaseException as exc:  # BaseException also catches Ctrl+C, so it's logged too
        finish_run(conn, run_id, "failed", counts, f"{type(exc).__name__}: {exc}"[:1000])
        raise

    finish_run(conn, run_id, "success", counts)
    return SyncResult(run_id, counts)


RAW_TABLES = (
    "hubspot_companies",
    "hubspot_deals",
    "hubspot_notes",
    "hubspot_pipelines",
    "hubspot_properties",
)


def empty_raw_tables(conn: psycopg.Connection) -> None:
    """Full refresh: a normal sync only adds and updates, so records archived or deleted in
    HubSpot would otherwise linger here forever. raw.* is a copy, so emptying it loses nothing
    that the next sync doesn't bring back."""
    tables = sql.SQL(", ").join(sql.Identifier("raw", t) for t in RAW_TABLES)
    conn.execute(sql.SQL("TRUNCATE {}").format(tables))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Full sync from HubSpot into raw.* tables")
    parser.add_argument("--full-refresh", action="store_true", help="empty the raw tables first")
    args = parser.parse_args(argv)

    # autocommit=True: each `with conn.transaction()` block is then a real, separate commit.
    with (
        psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn,
        HubSpotClient.from_env() as hs,
    ):
        if args.full_refresh:
            empty_raw_tables(conn)
            print("Emptied raw tables (full refresh).")
        result = run_sync(conn, hs)
    print(f"Sync run {result.run_id} finished: {result.counts}")


if __name__ == "__main__":
    main()
