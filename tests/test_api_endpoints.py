"""API endpoint tests.

Validation tests need no database. Data tests use real Postgres (`just up`): they insert
made-up forecast and commission runs for 2099 inside one transaction and roll it back,
so the real data is never changed.
"""

import os
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from app import main

# Not using `with TestClient(...)`, so the startup DB check and pool (lifespan) don't run.
client = TestClient(main.app)


@pytest.fixture
def no_db() -> Iterator[None]:
    """For validation tests: a fake connection that fails loudly if anything queries it."""
    main.app.dependency_overrides[main.get_conn] = lambda: None
    yield
    main.app.dependency_overrides.clear()


# ---------- Bad input -> 422 (no database needed) ----------


@pytest.mark.parametrize(
    "url",
    [
        "/forecast?quarter=2026-08-15",  # a real date, but not a quarter start
        "/forecast?quarter=not-a-date",
        "/forecast/runs?limit=0",
        "/forecast/runs?limit=1000",
        "/comp/payouts?quarter=2026-13-01",
        "/comp/payouts/rep_01/lines",  # quarter is required here
    ],
)
def test_bad_input_returns_422(no_db: None, url: str) -> None:
    resp = client.get(url)
    assert resp.status_code == 422
    assert "detail" in resp.json()  # says what was wrong and where


def test_quarter_error_explains_the_rule(no_db: None) -> None:
    resp = client.get("/forecast?quarter=2026-08-15")
    assert "first day of a quarter" in str(resp.json()["detail"])


def test_chat_rejects_an_empty_question(no_db: None) -> None:
    assert client.post("/agent/chat", json={"question": ""}).status_code == 422


def test_chat_says_the_agent_is_not_built_yet(no_db: None) -> None:
    resp = client.post("/agent/chat", json={"question": "What's our P50?"})
    assert resp.status_code == 501
    assert "Step 6" in resp.json()["detail"]


# ---------- Real queries on made-up 2099 runs ----------


@pytest.fixture
def db() -> Iterator[psycopg.Connection[dict[str, Any]]]:
    if "DATABASE_URL" not in os.environ:
        pytest.skip("DATABASE_URL not set (no .env?)")
    with psycopg.connect(
        os.environ["DATABASE_URL"], row_factory=dict_row, connect_timeout=3
    ) as conn:
        conn.execute(
            """INSERT INTO marts.fct_forecast_runs (method, as_of, quarter_start, p50, closed_won,
                 open_pipeline, weighted_open, n_won, n_open, n_missing_amount, n_unknown_stage,
                 n_overdue_prior)
               VALUES ('weighted_pipeline', '2099-02-15', '2099-01-01', 2000, 1000, 7000, 1000,
                       1, 4, 2, 1, 0)"""
        )
        run_id = conn.execute(
            """INSERT INTO comp.commission_runs (as_of, plan_id, first_quarter, last_quarter,
                 total_payout)
               VALUES ('2099-05-10', 'test_plan', '2099-01-01', '2099-04-01', 20000)
               RETURNING run_id"""
        ).fetchone()["run_id"]  # type: ignore[index]
        for rep, bookings, attainment, payout in (
            ("test_rep_a", 160000, "1.0666", "20062.50"),
            ("test_rep_b", 0, "0", "0"),
        ):
            conn.execute(
                """INSERT INTO comp.rep_quarter_results VALUES
                   (%s, %s, '2099-01-01', %s, 150000, %s, %s)""",
                (run_id, rep, bookings, attainment, payout),
            )
        for deal_id, tier, credit, rate, payout in (
            ("test-deal", 1, 150000, "0.125", "18750.00"),
            ("test-deal", 2, 10000, "0.1875", "1875.00"),
        ):
            conn.execute(
                """INSERT INTO comp.commission_lines VALUES
                   (%s, 'test_rep_a', %s, '2099-01-01', '2099-02-01', %s, %s, %s, %s,
                    'test_plan', 1)""",
                (run_id, deal_id, tier, credit, rate, payout),
            )
        main.app.dependency_overrides[main.get_conn] = lambda: conn
        yield conn
        main.app.dependency_overrides.clear()
        conn.rollback()


def test_latest_forecast(db: psycopg.Connection[dict[str, Any]]) -> None:
    body = client.get("/forecast?quarter=2099-01-01").json()
    assert body["p50"] == "2000.00"  # money arrives as a string: no float rounding
    assert body["n_unknown_stage"] == 1


def test_latest_comp_run_lists_its_quarters(db: psycopg.Connection[dict[str, Any]]) -> None:
    body = client.get("/comp/runs/latest").json()
    assert body["quarters"] == ["2099-01-01", "2099-04-01"]


def test_payouts_for_a_quarter(db: psycopg.Connection[dict[str, Any]]) -> None:
    body = client.get("/comp/payouts?quarter=2099-01-01").json()

    assert body["is_complete"] is True  # as_of 2099-05-10 is after Q1
    assert body["total_payout"] == "20062.50"
    assert [r["rep_id"] for r in body["reps"]] == ["test_rep_a", "test_rep_b"]  # by attainment
    assert body["reps"][0]["n_deals"] == 1
    assert body["reps"][1]["payout"] == "0.00"  # a rep who booked nothing still shows up


def test_payouts_default_to_the_runs_current_quarter(
    db: psycopg.Connection[dict[str, Any]],
) -> None:
    body = client.get("/comp/payouts").json()
    assert body["quarter_start"] == "2099-04-01"
    assert body["is_complete"] is False


def test_payouts_outside_the_run_are_404(db: psycopg.Connection[dict[str, Any]]) -> None:
    assert client.get("/comp/payouts?quarter=2098-01-01").status_code == 404


def test_lines_explain_a_payout_deal_by_deal(db: psycopg.Connection[dict[str, Any]]) -> None:
    lines = client.get("/comp/payouts/test_rep_a/lines?quarter=2099-01-01").json()
    assert [(line["tier"], line["credit"], line["payout"]) for line in lines] == [
        (1, "150000.00", "18750.00"),
        (2, "10000.00", "1875.00"),
    ]
    assert sum(Decimal(line["payout"]) for line in lines) == Decimal("20625.00")


def test_unknown_rep_has_no_lines(db: psycopg.Connection[dict[str, Any]]) -> None:
    assert client.get("/comp/payouts/nobody/lines?quarter=2099-01-01").json() == []


def test_quarter_helper() -> None:
    assert main.next_quarter(date(2026, 10, 1)) == date(2027, 1, 1)
