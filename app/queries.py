"""Every SQL statement the API runs. Read-only: the API never writes to the database."""

LATEST_FORECAST = """
SELECT run_id, created_at, method, as_of, quarter_start, p10, p50, p90,
       closed_won, open_pipeline, weighted_open,
       n_won, n_open, n_missing_amount, n_unknown_stage, n_overdue_prior
FROM marts.fct_forecast_runs
WHERE %(quarter)s::date IS NULL OR quarter_start = %(quarter)s::date
ORDER BY run_id DESC
LIMIT 1
"""

FORECAST_RUNS = """
SELECT run_id, created_at, method, as_of, quarter_start, p10, p50, p90,
       closed_won, open_pipeline, weighted_open,
       n_won, n_open, n_missing_amount, n_unknown_stage, n_overdue_prior
FROM marts.fct_forecast_runs
ORDER BY run_id DESC
LIMIT %(limit)s
"""

LATEST_COMP_RUN = """
SELECT run_id, created_at, as_of, plan_id, first_quarter, last_quarter, total_payout
FROM comp.commission_runs
ORDER BY run_id DESC
LIMIT 1
"""

REP_PAYOUTS = """
SELECT r.rep_id,
       COALESCE(d.rep_name, r.rep_id) AS rep_name,
       r.bookings, r.quota, r.attainment, r.payout,
       (SELECT count(DISTINCT l.deal_id)
          FROM comp.commission_lines AS l
         WHERE l.run_id = r.run_id AND l.rep_id = r.rep_id
           AND l.quarter_start = r.quarter_start) AS n_deals
FROM comp.rep_quarter_results AS r
LEFT JOIN marts.dim_reps AS d ON d.rep_id = r.rep_id
WHERE r.run_id = %(run_id)s AND r.quarter_start = %(quarter)s
ORDER BY r.attainment DESC, r.rep_id
"""

COMMISSION_LINES = """
SELECT l.deal_id, f.deal_name, l.close_date, l.tier, l.credit, l.rate, l.payout,
       l.plan_id, l.plan_version
FROM comp.commission_lines AS l
LEFT JOIN marts.fct_deals AS f ON f.deal_id = l.deal_id
WHERE l.run_id = %(run_id)s AND l.rep_id = %(rep_id)s AND l.quarter_start = %(quarter)s
ORDER BY l.close_date, l.deal_id, l.tier
"""
