-- Weighted pipeline forecast for the quarter containing %(as_of)s.
-- Originally a ✋ hand-written file; written by Claude at Chirayu's request (2026-11-22).
--
--   forecast = closed-won so far this quarter
--            + sum(open amount x stage probability) for open deals closing this quarter
--
-- Messy data is counted, not hidden:
--   - open deal with no / unreadable amount  -> counts as $0, reported in n_missing_amount
--   - open deal in a stage we can't find      -> counts at 0%%, reported in n_unknown_stage
--   - still-open deal whose close date is in an EARLIER quarter -> excluded from this quarter,
--     reported in n_overdue_prior (a rep needs to update or close it)
--
-- Caveat: this reads the CURRENT state of every deal, so it's only meaningful with
-- as_of = today. Backtesting a past date needs stage history (weekend 3).

WITH params AS (
    SELECT
        %(as_of)s::date                                AS as_of,
        date_trunc('quarter', %(as_of)s::date)::date   AS quarter_start
),

quarter_deals AS (
    SELECT d.*
    FROM marts.fct_deals AS d
    CROSS JOIN params AS p
    WHERE d.close_quarter = p.quarter_start
),

won AS (
    SELECT
        COALESCE(sum(amount), 0)  AS closed_won,
        count(*)                  AS n_won
    FROM quarter_deals AS d
    CROSS JOIN params AS p
    WHERE d.is_won
      AND d.close_date <= p.as_of          -- "to date": nothing won after as_of
),

open_deals AS (
    SELECT
        COALESCE(sum(COALESCE(amount, 0)), 0)                             AS open_pipeline,
        COALESCE(sum(COALESCE(amount, 0) * COALESCE(probability, 0)), 0)  AS weighted_open,
        count(*)                                                          AS n_open,
        count(*) FILTER (WHERE amount IS NULL)                            AS n_missing_amount,
        count(*) FILTER (WHERE NOT is_known_stage)                        AS n_unknown_stage
    FROM quarter_deals
    WHERE is_open
),

overdue AS (
    SELECT count(*) AS n_overdue_prior
    FROM marts.fct_deals AS d
    CROSS JOIN params AS p
    WHERE d.is_open
      AND d.close_date < p.quarter_start
)

SELECT
    p.as_of,
    p.quarter_start,
    w.closed_won,
    w.n_won,
    o.open_pipeline,
    round(o.weighted_open, 2)                 AS weighted_open,
    round(w.closed_won + o.weighted_open, 2)  AS forecast,
    o.n_open,
    o.n_missing_amount,
    o.n_unknown_stage,
    v.n_overdue_prior
FROM params AS p
CROSS JOIN won AS w
CROSS JOIN open_deals AS o
CROSS JOIN overdue AS v
