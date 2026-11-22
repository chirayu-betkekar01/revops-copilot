-- Step 3: tables the HubSpot sync writes to.
-- Safe to run any number of times (IF NOT EXISTS), so `just migrate` never needs a DB wipe.

-- raw.*: one row per HubSpot record, JSON stored exactly as the API returned it.
-- Typed columns (amount, stage, close date...) come later, as views in Step 4.

CREATE TABLE IF NOT EXISTS raw.hubspot_companies (
    hubspot_id  text        PRIMARY KEY,           -- HubSpot's record id: the upsert key
    payload     jsonb       NOT NULL,              -- the whole record: id, properties, associations
    synced_at   timestamptz NOT NULL DEFAULT now() -- when we last saved this row
);

CREATE TABLE IF NOT EXISTS raw.hubspot_deals (
    hubspot_id  text        PRIMARY KEY,
    payload     jsonb       NOT NULL,
    synced_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS raw.hubspot_notes (
    hubspot_id  text        PRIMARY KEY,
    payload     jsonb       NOT NULL,
    synced_at   timestamptz NOT NULL DEFAULT now()
);

-- Deal pipelines: stage ids, labels, order and win probability (used by the Step 4 forecast).
CREATE TABLE IF NOT EXISTS raw.hubspot_pipelines (
    hubspot_id  text        PRIMARY KEY,
    payload     jsonb       NOT NULL,
    synced_at   timestamptz NOT NULL DEFAULT now()
);

-- ops.sync_runs: one row per sync run, so you can always answer "did last night's sync work?"
CREATE TABLE IF NOT EXISTS ops.sync_runs (
    run_id       bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    started_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz,
    status       text        NOT NULL CHECK (status IN ('running', 'success', 'failed')),
    row_counts   jsonb       NOT NULL DEFAULT '{}'::jsonb, -- e.g. {"deals": 2000, "notes": 6439}
    error        text
);
