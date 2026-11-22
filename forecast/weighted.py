"""Weighted pipeline forecast (MVP method): compute it, store it, print it.

    just forecast                    # for the quarter containing today
    just forecast --as-of 2026-11-22

All the maths is in sql/forecast/weighted_pipeline.sql; this file only runs it and saves the
result to marts.fct_forecast_runs, so every number shown anywhere can be traced to a run.
"""

import argparse
import os
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg.rows import class_row

METHOD = "weighted_pipeline"
FORECAST_SQL_PATH = Path("sql/forecast/weighted_pipeline.sql")

INSERT_FORECAST_RUN = """
INSERT INTO marts.fct_forecast_runs (
    method, as_of, quarter_start, p50,
    closed_won, open_pipeline, weighted_open,
    n_won, n_open, n_missing_amount, n_unknown_stage, n_overdue_prior
) VALUES (
    %(method)s, %(as_of)s, %(quarter_start)s, %(forecast)s,
    %(closed_won)s, %(open_pipeline)s, %(weighted_open)s,
    %(n_won)s, %(n_open)s, %(n_missing_amount)s, %(n_unknown_stage)s, %(n_overdue_prior)s
)
RETURNING run_id
"""


@dataclass(frozen=True)
class WeightedForecast:
    as_of: date
    quarter_start: date
    closed_won: Decimal
    n_won: int
    open_pipeline: Decimal
    weighted_open: Decimal
    forecast: Decimal
    n_open: int
    n_missing_amount: int
    n_unknown_stage: int
    n_overdue_prior: int


def compute(conn: psycopg.Connection, as_of: date) -> WeightedForecast:
    query = FORECAST_SQL_PATH.read_text(encoding="utf-8")
    with conn.cursor(row_factory=class_row(WeightedForecast)) as cur:
        row = cur.execute(query, {"as_of": as_of}).fetchone()
    assert row is not None  # the query always returns exactly one row
    return row


def save(conn: psycopg.Connection, result: WeightedForecast) -> int:
    row = conn.execute(INSERT_FORECAST_RUN, {"method": METHOD, **asdict(result)}).fetchone()
    assert row is not None
    return int(row[0])


def quarter_label(d: date) -> str:
    return f"{d.year} Q{(d.month - 1) // 3 + 1}"


def report(run_id: int, f: WeightedForecast) -> str:
    lines = [
        f"Forecast run {run_id}: {quarter_label(f.quarter_start)} as of {f.as_of}",
        f"  Closed won so far:  ${f.closed_won:>13,.2f}  ({f.n_won} deals)",
        f"  Open pipeline:      ${f.open_pipeline:>13,.2f}  ({f.n_open} deals)",
        f"  Weighted open:      ${f.weighted_open:>13,.2f}",
        f"  FORECAST:           ${f.forecast:>13,.2f}",
    ]
    warnings = [
        (f.n_missing_amount, "open deals have no amount (counted as $0)"),
        (f.n_unknown_stage, "open deals are in an unknown stage (counted at 0%)"),
        (f.n_overdue_prior, "deals are still open but were due in an earlier quarter (excluded)"),
    ]
    lines += [f"  ! {n} {text}" for n, text in warnings if n]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Weighted pipeline forecast")
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    args = parser.parse_args(argv)

    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        result = compute(conn, args.as_of)
        run_id = save(conn, result)
    print(report(run_id, result))


if __name__ == "__main__":
    main()
