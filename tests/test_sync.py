"""Sync tests. Paging uses a fake HubSpot; the idempotency test also needs Postgres (`just up`)."""

import os
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import psycopg
import pytest

from seed.hubspot_client import HubSpotClient
from sync import hubspot as sync
from sync.migrate import migrate

DEALS = sync.SyncObject("deals", "hubspot_deals", ("dealname", "amount"), ("companies",))


def fake_hubspot(n_deals: int, seen_urls: list[str] | None = None) -> HubSpotClient:
    """A fake HubSpot with `n_deals` deals (ids test-1, test-2, ...) served 100 per page."""
    deals = [{"id": f"test-{i}", "properties": {"dealname": f"Deal {i}"}} for i in range(n_deals)]

    def handler(request: httpx.Request) -> httpx.Response:
        if seen_urls is not None:
            seen_urls.append(str(request.url))
        if request.url.path == "/crm/v3/pipelines/deals":
            return httpx.Response(200, json={"results": [{"id": "test-pipeline", "stages": []}]})
        start = int(request.url.params.get("after", 0))
        page = deals[start : start + sync.PAGE_SIZE]
        body: dict[str, object] = {"results": page}
        if start + sync.PAGE_SIZE < len(deals):
            body["paging"] = {"next": {"after": str(start + sync.PAGE_SIZE)}}
        return httpx.Response(200, json=body)

    return HubSpotClient("test-token", transport=httpx.MockTransport(handler))


# ---------- Paging (no database) ----------


def test_iter_pages_follows_the_cursor_to_the_end() -> None:
    urls: list[str] = []
    pages = list(sync.iter_pages(fake_hubspot(250, urls), DEALS))

    assert [len(p) for p in pages] == [100, 100, 50]
    afters = [parse_qs(urlparse(u).query).get("after") for u in urls]
    assert afters == [None, ["100"], ["200"]]


def test_iter_pages_asks_for_properties_and_associations() -> None:
    urls: list[str] = []
    list(sync.iter_pages(fake_hubspot(1, urls), DEALS))

    query = parse_qs(urlparse(urls[0]).query)
    assert query["properties"] == ["dealname,amount"]
    assert query["associations"] == ["companies"]


# ---------- Loading the hand-written SQL ----------


def test_load_upsert_sql_rejects_a_file_with_only_comments(tmp_path: Path) -> None:
    path = tmp_path / "upsert.sql"
    path.write_text("-- just a comment\n-- another\n", encoding="utf-8")

    with pytest.raises(SystemExit, match="no SQL yet"):
        sync.load_upsert_sql(path)


def test_load_upsert_sql_drops_comment_lines(tmp_path: Path) -> None:
    path = tmp_path / "upsert.sql"
    path.write_text("-- header\nSELECT 1\n-- trailer\n", encoding="utf-8")

    assert sync.load_upsert_sql(path) == "SELECT 1"


# ---------- Idempotency against real Postgres ----------


def upsert_is_written() -> bool:
    try:
        sync.load_upsert_sql()
    except SystemExit:
        return False
    return True


@pytest.fixture
def db() -> Iterator[psycopg.Connection]:
    if "DATABASE_URL" not in os.environ:
        pytest.skip("DATABASE_URL not set (no .env?)")
    if not upsert_is_written():
        pytest.skip("sql/sync/upsert_raw.sql isn't written yet")
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, connect_timeout=3) as conn:
        migrate(conn)
        yield conn
        # Clean up everything this test created, so the real data stays untouched.
        conn.execute("DELETE FROM raw.hubspot_deals WHERE hubspot_id LIKE 'test-%'")
        conn.execute("DELETE FROM raw.hubspot_pipelines WHERE hubspot_id LIKE 'test-%'")
        conn.execute("DELETE FROM ops.sync_runs WHERE row_counts->>'deals' = '150'")


def test_running_the_sync_twice_creates_no_duplicates(db: psycopg.Connection) -> None:
    first = sync.run_sync(db, fake_hubspot(150), objects=[DEALS])
    second = sync.run_sync(db, fake_hubspot(150), objects=[DEALS])

    row = db.execute("SELECT count(*) FROM raw.hubspot_deals WHERE hubspot_id LIKE 'test-%'")
    assert row.fetchone() == (150,)  # not 300
    assert first.counts["deals"] == second.counts["deals"] == 150

    statuses = db.execute(
        "SELECT status FROM ops.sync_runs WHERE run_id IN (%s, %s)",
        (first.run_id, second.run_id),
    ).fetchall()
    assert statuses == [("success",), ("success",)]
