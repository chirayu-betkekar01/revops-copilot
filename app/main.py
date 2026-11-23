"""RevOps Copilot API: read-only access to the stored forecast and commission runs.

    GET  /health                          API + database status
    GET  /forecast                        latest forecast run (optionally for one quarter)
    GET  /forecast/runs                   forecast history, newest first
    GET  /comp/runs/latest                the latest commission run and the quarters it covers
    GET  /comp/payouts                    every rep's payout for one quarter
    GET  /comp/payouts/{rep_id}/lines     the commission lines (deal by deal) behind a payout
    POST /agent/chat                      ask the forecast analyst agent (Amazon Bedrock)

Interactive docs: /docs. All numbers come from stored runs (`just forecast`, `just comp`);
the API never computes or invents a figure itself.
"""

import logging
import os
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from typing import Annotated, Any

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool, PoolTimeout

from agents.analyst import AgentResult, AgentUnavailable, ask, log_result
from app import queries
from app.models import (
    ChatRequest,
    ChatResponse,
    CommissionLine,
    CompRun,
    ErrorResponse,
    ForecastRun,
    PayoutsResponse,
    QuarterStart,
    RepPayout,
    ToolCallOut,
)

logger = logging.getLogger("uvicorn.error")


def check_database() -> None:
    """Open a connection and run SELECT 1. Raises psycopg.OperationalError if the DB is down."""
    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=2) as conn:
        conn.execute("SELECT 1")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Fail fast: if the database isn't reachable when the API starts, crash with a clear
    # error instead of starting up "healthy" and failing on the first real request.
    check_database()
    logger.info("Database reachable at startup")
    # A small pool of reusable connections. Every session is READ ONLY at the database level,
    # so even a bug in this API can't change data.
    pool = ConnectionPool(
        os.environ["DATABASE_URL"],
        min_size=1,
        max_size=5,
        timeout=5,
        kwargs={"options": "-c default_transaction_read_only=on", "row_factory": dict_row},
        open=False,
    )
    pool.open(wait=True, timeout=10)
    app.state.pool = pool
    yield
    pool.close()


app = FastAPI(title="RevOps Copilot API", lifespan=lifespan)

NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"model": ErrorResponse}}


@app.exception_handler(psycopg.OperationalError)
@app.exception_handler(PoolTimeout)
async def database_unavailable(request: Request, exc: Exception) -> JSONResponse:
    """Database down mid-request: say so with a 503 instead of a bare 500."""
    logger.warning("Database unavailable on %s: %s", request.url.path, exc)
    return JSONResponse(status_code=503, content={"detail": "Database unavailable"})


def get_conn(request: Request) -> Iterator[psycopg.Connection[dict[str, Any]]]:
    """One pooled connection per request (FastAPI dependency; tests swap it out)."""
    with request.app.state.pool.connection() as conn:
        yield conn


Conn = Annotated[psycopg.Connection[dict[str, Any]], Depends(get_conn)]


@app.get("/health")
def health(response: Response) -> dict[str, str]:
    """Report whether the API is up and whether it can reach the database right now."""
    try:
        check_database()
    except psycopg.OperationalError as exc:
        # Full error goes to the logs (`just logs`); the response stays generic.
        logger.warning("Health check failed: %s", exc)
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"api": "ok", "database": "unreachable"}
    return {"api": "ok", "database": "ok"}


# ---------- Forecast ----------


@app.get("/forecast", responses=NOT_FOUND)
def latest_forecast(conn: Conn, quarter: QuarterStart | None = None) -> ForecastRun:
    """The most recent forecast run, optionally for one quarter (e.g. ?quarter=2026-10-01)."""
    row = conn.execute(queries.LATEST_FORECAST, {"quarter": quarter}).fetchone()
    if row is None:
        raise HTTPException(404, "No forecast runs yet. Run `just forecast`.")
    return ForecastRun(**row)


@app.get("/forecast/runs")
def forecast_runs(conn: Conn, limit: Annotated[int, Query(ge=1, le=100)] = 20) -> list[ForecastRun]:
    """Forecast history, newest first, so you can see how the number moved."""
    rows = conn.execute(queries.FORECAST_RUNS, {"limit": limit}).fetchall()
    return [ForecastRun(**row) for row in rows]


# ---------- Comp ----------


def next_quarter(quarter: date) -> date:
    month = quarter.month + 3
    return date(quarter.year + (month - 1) // 12, (month - 1) % 12 + 1, 1)


def quarters_between(first: date, last: date) -> list[date]:
    quarters, current = [], first
    while current <= last:
        quarters.append(current)
        current = next_quarter(current)
    return quarters


def latest_comp_run_row(conn: psycopg.Connection[dict[str, Any]]) -> dict[str, Any]:
    row = conn.execute(queries.LATEST_COMP_RUN).fetchone()
    if row is None:
        raise HTTPException(404, "No commission runs yet. Run `just comp`.")
    return row


@app.get("/comp/runs/latest", responses=NOT_FOUND)
def latest_comp_run(conn: Conn) -> CompRun:
    row = latest_comp_run_row(conn)
    return CompRun(**row, quarters=quarters_between(row["first_quarter"], row["last_quarter"]))


@app.get("/comp/payouts", responses=NOT_FOUND)
def payouts(conn: Conn, quarter: QuarterStart | None = None) -> PayoutsResponse:
    """Every rep's bookings, attainment and payout for one quarter of the latest commission
    run. Defaults to the quarter containing the run's as-of date."""
    run = latest_comp_run_row(conn)
    as_of: date = run["as_of"]
    quarter = quarter or date(as_of.year, 3 * ((as_of.month - 1) // 3) + 1, 1)
    if not run["first_quarter"] <= quarter <= run["last_quarter"]:
        raise HTTPException(
            404, f"Run {run['run_id']} covers {run['first_quarter']} to {run['last_quarter']}"
        )

    rows = conn.execute(
        queries.REP_PAYOUTS, {"run_id": run["run_id"], "quarter": quarter}
    ).fetchall()
    reps = [RepPayout(**row) for row in rows]
    return PayoutsResponse(
        run_id=run["run_id"],
        as_of=as_of,
        quarter_start=quarter,
        is_complete=next_quarter(quarter) <= as_of,
        total_bookings=sum((r.bookings for r in reps), start=Decimal(0)),
        total_payout=sum((r.payout for r in reps), start=Decimal(0)),
        reps=reps,
    )


@app.get("/comp/payouts/{rep_id}/lines", responses=NOT_FOUND)
def commission_lines(conn: Conn, rep_id: str, quarter: QuarterStart) -> list[CommissionLine]:
    """The deal-by-deal lines behind one rep's payout: proof of how every dollar was earned."""
    run = latest_comp_run_row(conn)
    rows = conn.execute(
        queries.COMMISSION_LINES,
        {"run_id": run["run_id"], "rep_id": rep_id, "quarter": quarter},
    ).fetchall()
    return [CommissionLine(**row) for row in rows]


# ---------- Agent ----------


AgentRunner = Callable[[str], AgentResult]
AgentLogger = Callable[..., int]


def get_agent() -> AgentRunner:
    """The real agent (Bedrock + read-only tools). Tests swap in a fake."""
    return ask


def get_agent_logger() -> AgentLogger:
    return log_result


def try_log(
    log: AgentLogger, result: AgentResult | None, question: str, error: str | None
) -> int | None:
    """Audit-log the call; a logging failure must not hide the answer from the user."""
    try:
        return log(result, question=question, error=error)
    except psycopg.Error as exc:
        logger.warning("Could not write ops.agent_logs: %s", exc)
        return None


@app.post("/agent/chat", responses={503: {"model": ErrorResponse}})
def agent_chat(
    body: ChatRequest,
    agent: Annotated[AgentRunner, Depends(get_agent)],
    log: Annotated[AgentLogger, Depends(get_agent_logger)],
) -> ChatResponse:
    """Ask the forecast analyst. Every number in the answer comes from a tool call, and the
    tool calls (with the exact SQL) are returned so the answer can be checked."""
    try:
        result = agent(body.question)
    except AgentUnavailable as exc:
        try_log(log, None, body.question, str(exc))
        raise HTTPException(503, str(exc)) from exc

    log_id = try_log(log, result, body.question, None)
    return ChatResponse(
        answer=result.answer,
        status=result.status,
        model_id=result.model_id,
        turns=result.turns,
        tool_calls=[ToolCallOut(**asdict(call)) for call in result.tool_calls],
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        latency_ms=result.latency_ms,
        log_id=log_id,
    )
