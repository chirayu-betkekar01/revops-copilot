-- Step 5: comp plans, quotas and stored commission runs.
-- The plan YAML is the source of truth; these tables record exactly what each run used,
-- so any payout can be re-explained later even if the YAML has changed since.

-- One row per plan version, copied from comp/plans/*.yaml on every run.
CREATE TABLE IF NOT EXISTS comp.comp_plans (
    plan_id         text          NOT NULL,
    version         integer       NOT NULL,
    name            text          NOT NULL,
    effective_from  date          NOT NULL,
    ote             numeric(14,2) NOT NULL,
    base_salary     numeric(14,2) NOT NULL,
    annual_quota    numeric(14,2) NOT NULL,
    tiers           jsonb         NOT NULL,  -- [{floor, ceiling, multiplier}, ...]
    loaded_at       timestamptz   NOT NULL DEFAULT now(),
    PRIMARY KEY (plan_id, version)
);

-- One row per rep per quarter: the quota their attainment is measured against.
CREATE TABLE IF NOT EXISTS comp.quotas (
    rep_id         text          NOT NULL,
    quarter_start  date          NOT NULL,
    plan_id        text          NOT NULL,
    quota          numeric(14,2) NOT NULL,
    PRIMARY KEY (rep_id, quarter_start)
);

-- One row per `just comp` run.
CREATE TABLE IF NOT EXISTS comp.commission_runs (
    run_id         bigint        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at     timestamptz   NOT NULL DEFAULT now(),
    as_of          date          NOT NULL,   -- only deals closed won on or before this day
    plan_id        text          NOT NULL,
    first_quarter  date          NOT NULL,
    last_quarter   date          NOT NULL,
    total_payout   numeric(14,2) NOT NULL
);

-- One row per rep per quarter per run, including reps who booked nothing ($0).
CREATE TABLE IF NOT EXISTS comp.rep_quarter_results (
    run_id         bigint        NOT NULL REFERENCES comp.commission_runs (run_id),
    rep_id         text          NOT NULL,
    quarter_start  date          NOT NULL,
    bookings       numeric(14,2) NOT NULL,
    quota          numeric(14,2) NOT NULL,
    attainment     numeric       NOT NULL,   -- 1.0 = 100% of quota
    payout         numeric(14,2) NOT NULL,   -- = sum of this rep-quarter's commission_lines
    PRIMARY KEY (run_id, rep_id, quarter_start)
);

-- One row per deal per tier it fell into: the audit trail behind every payout.
-- A deal that crosses a tier boundary has two (or more) lines.
CREATE TABLE IF NOT EXISTS comp.commission_lines (
    run_id         bigint        NOT NULL REFERENCES comp.commission_runs (run_id),
    rep_id         text          NOT NULL,
    deal_id        text          NOT NULL,
    quarter_start  date          NOT NULL,
    close_date     date          NOT NULL,
    tier           integer       NOT NULL,
    credit         numeric(14,2) NOT NULL,   -- slice of the deal's amount in this tier
    rate           numeric       NOT NULL,   -- unrounded, e.g. 0.1875
    payout         numeric(14,2) NOT NULL,   -- credit x rate, rounded half up to the cent
    plan_id        text          NOT NULL,
    plan_version   integer       NOT NULL,
    PRIMARY KEY (run_id, deal_id, tier)
);
