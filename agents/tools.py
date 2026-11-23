"""The forecast analyst's tools. The model never computes a number itself: every figure in an
answer comes from one of these functions, which read Postgres as the read-only `agent_ro` user.

    run_sql                one guarded, read-only SELECT on the marts tables
    get_forecast_run       a stored forecast run (latest, for a quarter, or by id)
    compare_forecast_runs  two stored forecast runs side by side, with the differences

Model output is untrusted input: every tool argument is type-checked here before use.
"""

import os
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from typing import Any, LiteralString

import psycopg
from psycopg.rows import dict_row

from agents.sql_guard import MAX_ROWS, UnsafeSQLError, validate_select

JSON = dict[str, Any]
Connect = Callable[[], psycopg.Connection[dict[str, Any]]]

FORECAST_COLUMNS: LiteralString = """
run_id, created_at, method, as_of, quarter_start, p10, p50, p90, closed_won, open_pipeline,
weighted_open, n_won, n_open, n_missing_amount, n_unknown_stage, n_overdue_prior
"""
GET_FORECAST_RUN: LiteralString = (
    "SELECT" + FORECAST_COLUMNS + "FROM marts.fct_forecast_runs"
    " WHERE (%(run_id)s::bigint IS NULL OR run_id = %(run_id)s::bigint)"
    " AND (%(quarter)s::date IS NULL OR quarter_start = %(quarter)s::date)"
    " ORDER BY run_id DESC LIMIT 1"
)
GET_TWO_RUNS: LiteralString = (
    "SELECT" + FORECAST_COLUMNS + "FROM marts.fct_forecast_runs WHERE run_id = ANY(%(ids)s)"
)
COMPARED_FIELDS = ("p50", "closed_won", "open_pipeline", "weighted_open", "n_won", "n_open")

TOOL_SPECS: list[JSON] = [
    {
        "toolSpec": {
            "name": "run_sql",
            "description": (
                "Run ONE read-only PostgreSQL SELECT against the marts tables described in the "
                f"system prompt. Returns at most {MAX_ROWS} rows. Aggregate in SQL (SUM, COUNT, "
                "GROUP BY) instead of fetching many rows. Writes and other schemas are rejected."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "One SELECT statement."}
                    },
                    "required": ["query"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "get_forecast_run",
            "description": (
                "Get a stored bookings forecast run: the official forecast numbers. Use this for "
                "any question about the forecast (P50, closed won, weighted pipeline). With no "
                "arguments it returns the latest run."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "quarter": {
                            "type": "string",
                            "description": "First day of a quarter, e.g. 2026-10-01 (optional).",
                        },
                        "run_id": {"type": "integer", "description": "A run id (optional)."},
                    },
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "compare_forecast_runs",
            "description": (
                "Compare two stored forecast runs and return both plus the change in each number."
                " Use it to explain why the forecast moved between runs."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "run_id_a": {"type": "integer", "description": "The earlier run id."},
                        "run_id_b": {"type": "integer", "description": "The later run id."},
                    },
                    "required": ["run_id_a", "run_id_b"],
                }
            },
        }
    },
]


class ToolError(ValueError):
    """A problem the model can fix (bad argument, rejected SQL). Sent back as the tool result."""


def json_safe(value: Any) -> Any:
    """Bedrock tool results must be plain JSON: money stays exact as a string."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [json_safe(v) for v in value]
    return value


def with_quarter_label(run: dict[str, Any]) -> dict[str, Any]:
    """Add "2026 Q4" next to quarter_start, so the model never converts the date itself."""
    start: date = run["quarter_start"]
    return {**run, "quarter_label": f"{start.year} Q{(start.month - 1) // 3 + 1}"}


def agent_connect() -> psycopg.Connection[dict[str, Any]]:
    """Connect as the read-only agent role. The session limits repeat the role's own settings,
    so they hold even if the role was created without them."""
    return psycopg.connect(
        os.environ["DATABASE_URL"],
        user="agent_ro",
        password=os.environ["AGENT_DB_PASSWORD"],
        connect_timeout=5,
        options="-c default_transaction_read_only=on -c statement_timeout=5000",
        row_factory=dict_row,
    )


def optional_int(tool_input: JSON, key: str) -> int | None:
    value = tool_input.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolError(f"{key} must be an integer")
    return value


class AnalystTools:
    specs = TOOL_SPECS

    def __init__(self, connect: Connect = agent_connect) -> None:
        self._connect = connect

    def run(self, name: str, tool_input: JSON) -> JSON:
        handlers: dict[str, Callable[[JSON], JSON]] = {
            "run_sql": self.run_sql,
            "get_forecast_run": self.get_forecast_run,
            "compare_forecast_runs": self.compare_forecast_runs,
        }
        if name not in handlers:
            raise ToolError(f"Unknown tool {name!r}. Available: {', '.join(handlers)}")
        return handlers[name](tool_input)

    def run_sql(self, tool_input: JSON) -> JSON:
        query = tool_input.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ToolError("query must be a non-empty string")
        try:
            safe_query = validate_select(query)
        except UnsafeSQLError as exc:
            raise ToolError(f"Query rejected: {exc}") from exc
        try:
            with self._connect() as conn, conn.cursor() as cur:
                # The guard produced this SQL from a parsed SELECT; it's not user-concatenated.
                cur.execute(safe_query)
                rows = cur.fetchall()
                columns = [col.name for col in cur.description or []]
        except psycopg.Error as exc:
            # Permission denied, timeout, bad column...: tell the model so it can fix the query.
            raise ToolError(f"Database error: {str(exc).splitlines()[0]}") from exc
        return {
            "sql": safe_query,
            "columns": columns,
            "rows": json_safe([[row[c] for c in columns] for row in rows]),
            "row_count": len(rows),
            "truncated": len(rows) >= MAX_ROWS,
        }

    def get_forecast_run(self, tool_input: JSON) -> JSON:
        run_id = optional_int(tool_input, "run_id")
        quarter = tool_input.get("quarter")
        if quarter is not None:
            try:
                quarter = date.fromisoformat(str(quarter))
            except ValueError as exc:
                raise ToolError("quarter must be a date like 2026-10-01") from exc
        with self._connect() as conn:
            row = conn.execute(GET_FORECAST_RUN, {"run_id": run_id, "quarter": quarter}).fetchone()
        if row is None:
            raise ToolError("No forecast run matches. Try without arguments for the latest run.")
        return dict(json_safe(with_quarter_label(row)))

    def compare_forecast_runs(self, tool_input: JSON) -> JSON:
        run_a, run_b = optional_int(tool_input, "run_id_a"), optional_int(tool_input, "run_id_b")
        if run_a is None or run_b is None:
            raise ToolError("run_id_a and run_id_b are both required")
        with self._connect() as conn:
            rows = {r["run_id"]: r for r in conn.execute(GET_TWO_RUNS, {"ids": [run_a, run_b]})}
        missing = [str(i) for i in (run_a, run_b) if i not in rows]
        if missing:
            raise ToolError(f"No forecast run with id {', '.join(missing)}")
        a, b = with_quarter_label(rows[run_a]), with_quarter_label(rows[run_b])
        changes = {field: b[field] - a[field] for field in COMPARED_FIELDS}
        return dict(json_safe({"run_a": a, "run_b": b, "change_b_minus_a": changes}))
