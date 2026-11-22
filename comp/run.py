"""Run the comp engine on real won deals and store every result.

    just comp                    # all quarters up to today
    just comp --as-of 2026-09-30

The maths is all in comp/engine.py. This file only loads inputs from Postgres and the plan
YAML, calls the engine, and saves plans, quotas, per-rep results and commission lines.
"""

import argparse
import os
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg.rows import class_row
from psycopg.types.json import Jsonb

from comp.engine import (
    CreditedDeal,
    Plan,
    RepQuarterResult,
    compute_payouts,
    load_plan,
    quarter_end,
    quarter_start,
    quarters_between,
)

PLAN_PATH = Path("comp/plans/standard_ae.yaml")

# Credit rule: 100% to the deal owner, on the closed-won date.
LOAD_WON_DEALS = """
SELECT deal_id, rep_id, amount, close_date
FROM marts.fct_deals
WHERE is_won AND amount IS NOT NULL AND rep_id IS NOT NULL AND close_date <= %(as_of)s
"""
LOAD_REPS = "SELECT rep_id, rep_name FROM marts.dim_reps WHERE is_active ORDER BY display_order"

UPSERT_PLAN_VERSION = """
INSERT INTO comp.comp_plans
    (plan_id, version, name, effective_from, ote, base_salary, annual_quota, tiers)
VALUES (%(plan_id)s, %(version)s, %(name)s, %(effective_from)s, %(ote)s, %(base_salary)s,
        %(annual_quota)s, %(tiers)s)
ON CONFLICT (plan_id, version) DO UPDATE SET
    name = EXCLUDED.name, effective_from = EXCLUDED.effective_from, ote = EXCLUDED.ote,
    base_salary = EXCLUDED.base_salary, annual_quota = EXCLUDED.annual_quota,
    tiers = EXCLUDED.tiers, loaded_at = now()
"""
UPSERT_QUOTA = """
INSERT INTO comp.quotas (rep_id, quarter_start, plan_id, quota)
VALUES (%(rep_id)s, %(quarter_start)s, %(plan_id)s, %(quota)s)
ON CONFLICT (rep_id, quarter_start) DO UPDATE SET plan_id = EXCLUDED.plan_id, quota = EXCLUDED.quota
"""
INSERT_RUN = """
INSERT INTO comp.commission_runs (as_of, plan_id, first_quarter, last_quarter, total_payout)
VALUES (%s, %s, %s, %s, %s) RETURNING run_id
"""
INSERT_RESULT = """
INSERT INTO comp.rep_quarter_results
    (run_id, rep_id, quarter_start, bookings, quota, attainment, payout)
VALUES (%(run_id)s, %(rep_id)s, %(quarter_start)s, %(bookings)s, %(quota)s,
        %(attainment)s, %(payout)s)
"""
INSERT_LINE = """
INSERT INTO comp.commission_lines
    (run_id, rep_id, deal_id, quarter_start, close_date, tier, credit, rate, payout,
     plan_id, plan_version)
VALUES (%(run_id)s, %(rep_id)s, %(deal_id)s, %(quarter_start)s, %(close_date)s, %(tier)s,
        %(credit)s, %(rate)s, %(payout)s, %(plan_id)s, %(plan_version)s)
"""


def save(conn: psycopg.Connection, plan: Plan, as_of: date, results: list[RepQuarterResult]) -> int:
    """Store the whole run in one transaction: a run is either fully saved or not at all."""
    quarters = sorted({r.quarter_start for r in results})
    total = sum((r.payout for r in results), Decimal(0))
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            UPSERT_PLAN_VERSION,
            [
                {
                    **{k: v for k, v in asdict(version).items() if k != "tiers"},
                    "name": plan.name,
                    "tiers": Jsonb(
                        [{k: str(x) for k, x in asdict(t).items()} for t in version.tiers]
                    ),
                }
                for version in plan.versions
            ],
        )
        cur.executemany(
            UPSERT_QUOTA,
            [
                {"rep_id": r.rep_id, "quarter_start": r.quarter_start, "plan_id": plan.plan_id,
                 "quota": r.quota}
                for r in results
            ],
        )  # fmt: skip
        row = cur.execute(
            INSERT_RUN, (as_of, plan.plan_id, quarters[0], quarters[-1], total)
        ).fetchone()
        assert row is not None
        run_id = int(row[0])
        cur.executemany(
            INSERT_RESULT,
            [
                {k: v for k, v in asdict(r).items() if k != "lines"} | {"run_id": run_id}
                for r in results
            ],
        )
        cur.executemany(
            INSERT_LINE,
            [asdict(line) | {"run_id": run_id} for r in results for line in r.lines],
        )
    return run_id


def report(results: list[RepQuarterResult], names: dict[str, str], as_of: date) -> str:
    """Summary of the last complete quarter and the current quarter to date."""
    current = quarter_start(as_of)
    previous = max((r.quarter_start for r in results if r.quarter_start < current), default=None)
    lines = []
    for quarter in (previous, current):
        if quarter is None:
            continue
        rows = [r for r in results if r.quarter_start == quarter]
        label = "to date" if quarter == current else "final"
        lines.append(f"\n{quarter} to {quarter_end(quarter)} ({label})")
        lines.append(f"  {'Rep':<16}{'Bookings':>14}{'Attainment':>12}{'Payout':>13}")
        for r in sorted(rows, key=lambda r: r.attainment, reverse=True):
            lines.append(
                f"  {names.get(r.rep_id, r.rep_id):<16}${r.bookings:>12,.0f}"
                f"{r.attainment:>11.0%} ${r.payout:>11,.2f}"
            )
        lines.append(f"  {'Total':<16}{'':>26} ${sum(r.payout for r in rows):>11,.2f}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Compute and store commission payouts")
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    args = parser.parse_args(argv)

    plan = load_plan(PLAN_PATH)
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        with conn.cursor(row_factory=class_row(CreditedDeal)) as cur:
            deals = cur.execute(LOAD_WON_DEALS, {"as_of": args.as_of}).fetchall()
        names: dict[str, str] = dict(conn.execute(LOAD_REPS).fetchall())

        quarters = quarters_between(plan.versions[0].effective_from, args.as_of)
        results = compute_payouts(deals, plan, list(names), quarters)
        run_id = save(conn, plan, args.as_of, results)

    skipped = [d for d in deals if d.rep_id not in names]
    print(f"Commission run {run_id}: {len(deals)} won deals, {len(quarters)} quarters")
    if skipped:
        print(f"  ! {len(skipped)} won deals belong to reps not on the plan (not paid)")
    print(report(results, names, args.as_of))


if __name__ == "__main__":
    main()
