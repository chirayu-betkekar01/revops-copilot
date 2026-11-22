"""Forecast + views tests against real Postgres (`just up`), using made-up deals in 2099 Q1.

Everything runs inside one transaction that is rolled back at the end, so the real synced
data is never changed. Using 2099 keeps the real 2025-2026 deals out of the totals.
"""

import os
from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import psycopg
import pytest
from psycopg.types.json import Jsonb

from forecast.weighted import compute
from sync.migrate import migrate

AS_OF = date(2099, 2, 15)  # 2099 Q1 runs Jan 1 - Mar 31

TEST_PIPELINE = {
    "id": "test-pipeline",
    "label": "Test pipeline",
    "stages": [
        {"id": "t_early", "label": "Early", "displayOrder": 0,
         "metadata": {"probability": "0.2", "isClosed": "false"}},
        {"id": "t_mid", "label": "Mid", "displayOrder": 1,
         "metadata": {"probability": "0.5", "isClosed": "false"}},
        {"id": "t_won", "label": "Won", "displayOrder": 2,
         "metadata": {"probability": "1.0", "isClosed": "true"}},
        {"id": "t_lost", "label": "Lost", "displayOrder": 3,
         "metadata": {"probability": "0.0", "isClosed": "true"}},
    ],
}  # fmt: skip


def insert_deal(
    conn: psycopg.Connection, deal_id: str, stage: str, amount: str | None, close: str
) -> None:
    properties = {
        "dealname": deal_id,
        "dealstage": stage,
        "pipeline": "test-pipeline",
        "closedate": f"{close}T00:00:00Z",
        "createdate": "2099-01-01T00:00:00Z",
        "rep_id": "rep_01",
    }
    if amount is not None:
        properties["amount"] = amount
    conn.execute(
        "INSERT INTO raw.hubspot_deals (hubspot_id, payload) VALUES (%s, %s)",
        (deal_id, Jsonb({"id": deal_id, "properties": properties})),
    )


@pytest.fixture
def db() -> Iterator[psycopg.Connection]:
    if "DATABASE_URL" not in os.environ:
        pytest.skip("DATABASE_URL not set (no .env?)")
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, connect_timeout=3) as conn:
        migrate(conn)  # make sure tables and views exist
        conn.autocommit = False  # from here on, everything is one transaction...
        conn.execute(
            "INSERT INTO raw.hubspot_pipelines (hubspot_id, payload) VALUES (%s, %s)",
            ("test-pipeline", Jsonb(TEST_PIPELINE)),
        )
        # In 2099 Q1:
        insert_deal(conn, "test-won", "t_won", "1000", "2099-01-10")  # counts: won to date
        insert_deal(conn, "test-won-later", "t_won", "500", "2099-03-20")  # after as_of: no
        insert_deal(conn, "test-mid", "t_mid", "2000", "2099-03-01")  # 2000 x 50% = 1000
        insert_deal(conn, "test-no-amount", "t_mid", None, "2099-03-01")  # break-it: null
        insert_deal(conn, "test-bad-amount", "t_early", "12,000", "2099-03-05")  # unreadable
        insert_deal(conn, "test-mystery", "mystery_stage", "5000", "2099-03-10")  # break-it
        insert_deal(conn, "test-lost", "t_lost", "3000", "2099-01-20")  # lost: ignored
        # Outside 2099 Q1:
        insert_deal(conn, "test-next-q", "t_mid", "7000", "2099-05-01")  # next quarter: no
        yield conn
        conn.rollback()  # ...which is thrown away here


def test_weighted_forecast_on_known_deals(db: psycopg.Connection) -> None:
    f = compute(db, AS_OF)

    assert f.quarter_start == date(2099, 1, 1)
    assert f.closed_won == Decimal("1000.00")
    assert f.n_won == 1
    assert f.weighted_open == Decimal("1000.00")  # only test-mid contributes
    assert f.forecast == Decimal("2000.00")
    assert f.open_pipeline == Decimal("7000.00")  # 2000 + 0 + 0 + 5000


def test_messy_deals_are_counted_not_hidden(db: psycopg.Connection) -> None:
    f = compute(db, AS_OF)

    assert f.n_open == 4  # mid, no-amount, bad-amount, mystery
    assert f.n_missing_amount == 2  # no-amount and the unreadable "12,000"
    assert f.n_unknown_stage == 1  # mystery_stage


def test_overdue_open_deals_from_earlier_quarters_are_excluded(db: psycopg.Connection) -> None:
    before = compute(db, AS_OF)
    insert_deal(db, "test-overdue", "t_mid", "4000", "2098-12-15")  # still open, due last year
    after = compute(db, AS_OF)

    assert after.n_overdue_prior == before.n_overdue_prior + 1
    assert after.forecast == before.forecast  # not counted in this quarter


def test_views_parse_and_flag_deals(db: psycopg.Connection) -> None:
    rows: dict[str, tuple[str | None, ...]] = dict(
        db.execute(
            "SELECT deal_id, (amount, is_known_stage, is_won, is_open) FROM marts.fct_deals"
            " WHERE deal_id IN ('test-won', 'test-bad-amount', 'test-mystery')"
        ).fetchall()
    )
    assert rows["test-won"] == ("1000.00", "t", "t", "f")
    assert rows["test-bad-amount"] == (None, "t", "f", "t")
    assert rows["test-mystery"] == ("5000.00", "f", "f", "t")
