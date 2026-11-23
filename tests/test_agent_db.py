"""Defense in depth against real Postgres: even with the SQL guard skipped entirely,
the agent's own database login must refuse writes, other schemas and slow queries.

Needs `just up` + `just migrate` with AGENT_DB_PASSWORD set (creates the agent_ro role).
"""

import os
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest

from agents.analyst import AgentResult, ToolCall, log_result
from agents.tools import AnalystTools, ToolError, agent_connect
from sync.migrate import migrate


@pytest.fixture
def agent_conn() -> Iterator[psycopg.Connection[dict[str, Any]]]:
    if not os.environ.get("DATABASE_URL") or not os.environ.get("AGENT_DB_PASSWORD"):
        pytest.skip("DATABASE_URL / AGENT_DB_PASSWORD not set")
    try:
        conn = agent_connect()
    except psycopg.OperationalError as exc:
        pytest.skip(f"agent_ro can't log in (run `just migrate`): {exc}")
    with conn:
        yield conn


def test_agent_can_still_read_views_after_they_are_recreated(
    agent_conn: psycopg.Connection[dict[str, Any]],
) -> None:
    """Regression: migrate() drops and recreates the views, which used to drop the agent's
    grants ("permission denied for view fct_deals" in the first live run)."""
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as admin:
        migrate(admin)
    out = AnalystTools().run("run_sql", {"query": "SELECT count(*) AS n FROM fct_deals"})
    assert out["columns"] == ["n"]


def test_agent_tools_read_marts(agent_conn: psycopg.Connection[dict[str, Any]]) -> None:
    out = AnalystTools().run("run_sql", {"query": "SELECT count(*) AS n FROM dim_reps"})
    assert out["columns"] == ["n"]
    assert out["sql"].endswith("LIMIT 200")


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM marts.fct_forecast_runs",
        "UPDATE marts.fct_forecast_runs SET p50 = 0",
        "CREATE TABLE marts.sneaky (x int)",
    ],
)
def test_role_refuses_writes_even_without_the_guard(
    agent_conn: psycopg.Connection[dict[str, Any]], statement: str
) -> None:
    with pytest.raises(psycopg.Error):  # read-only transaction / permission denied
        agent_conn.execute(statement)
    agent_conn.rollback()


@pytest.mark.parametrize(
    "table", ["raw.hubspot_deals", "comp.commission_lines", "ops.agent_logs", "staging.stg_deals"]
)
def test_role_cannot_read_other_schemas(
    agent_conn: psycopg.Connection[dict[str, Any]], table: str
) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        agent_conn.execute(f"SELECT * FROM {table} LIMIT 1")
    agent_conn.rollback()


def test_slow_queries_are_cancelled_after_5_seconds(
    agent_conn: psycopg.Connection[dict[str, Any]],
) -> None:
    with pytest.raises(psycopg.errors.QueryCanceled):
        agent_conn.execute("SELECT pg_sleep(8)")
    agent_conn.rollback()


def test_database_errors_reach_the_model_as_tool_errors(
    agent_conn: psycopg.Connection[dict[str, Any]],
) -> None:
    with pytest.raises(ToolError, match="Database error"):
        AnalystTools().run("run_sql", {"query": "SELECT no_such_column FROM fct_deals"})


def test_every_call_is_audit_logged(agent_conn: psycopg.Connection[dict[str, Any]]) -> None:
    result = AgentResult(
        question="test question",
        answer="test answer",
        status="answered",
        model_id="test-model",
        turns=2,
        tool_calls=[ToolCall("run_sql", {"query": "SELECT 1"}, ok=True)],
        input_tokens=10,
        output_tokens=5,
        latency_ms=123,
    )
    log_id = log_result(result, question="test question")
    with psycopg.connect(os.environ["DATABASE_URL"]) as admin:
        row = admin.execute(
            "SELECT status, tool_calls->0->>'name', input_tokens FROM ops.agent_logs"
            " WHERE log_id = %s",
            (log_id,),
        ).fetchone()
        admin.execute("DELETE FROM ops.agent_logs WHERE log_id = %s", (log_id,))
    assert row == ("answered", "run_sql", 10)
