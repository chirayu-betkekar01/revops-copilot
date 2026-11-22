-- Step 4: property definitions (for rep names) and stored forecast runs.

-- raw.hubspot_properties: HubSpot property definitions we need, e.g. the rep_id dropdown
-- whose options hold each rep's id (value) and name (label). hubspot_id = '<object>.<property>'.
CREATE TABLE IF NOT EXISTS raw.hubspot_properties (
    hubspot_id  text        PRIMARY KEY,
    payload     jsonb       NOT NULL,
    synced_at   timestamptz NOT NULL DEFAULT now()
);

-- marts.fct_forecast_runs: one row per forecast run per quarter per method.
-- Stored, not recomputed, so every number is auditable and changes week over week are visible.
CREATE TABLE IF NOT EXISTS marts.fct_forecast_runs (
    run_id             bigint        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at         timestamptz   NOT NULL DEFAULT now(),
    method             text          NOT NULL,        -- 'weighted_pipeline' (MVP)
    as_of              date          NOT NULL,        -- the "today" the forecast was made for
    quarter_start      date          NOT NULL,        -- first day of the forecast quarter
    p10                numeric(14,2),                 -- range methods only (weekend 3)
    p50                numeric(14,2) NOT NULL,        -- the forecast; single-number methods use this
    p90                numeric(14,2),
    -- How the number was built, so "why did it change?" can be answered:
    closed_won         numeric(14,2) NOT NULL,        -- already won this quarter
    open_pipeline      numeric(14,2) NOT NULL,        -- open amount closing this quarter
    weighted_open      numeric(14,2) NOT NULL,        -- open amount x stage probability
    n_won              integer       NOT NULL,
    n_open             integer       NOT NULL,
    n_missing_amount   integer       NOT NULL,        -- open deals with no amount (counted as $0)
    n_unknown_stage    integer       NOT NULL,        -- open deals in a stage we can't find (0%)
    n_overdue_prior    integer       NOT NULL         -- still-open deals that should have closed
                                                      -- in an earlier quarter (excluded)
);
