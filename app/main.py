"""RevOps Copilot API.

Step 1 only: prove the API container can reach the Postgres container.
Real endpoints (/forecast, /comp/payouts, /agent/chat) arrive in Step 7.
"""

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, Response, status

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
    yield


app = FastAPI(title="RevOps Copilot API", lifespan=lifespan)


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
