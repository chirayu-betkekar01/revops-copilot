-- Step 6: one row per analyst-agent call, so every answer can be audited later:
-- what was asked, which tools ran with what input, what it cost in tokens, how long it took.
-- (The read-only `agent_ro` login role is created by sync/migrate.py, because its password
-- comes from .env and SQL files can't read environment variables.)

CREATE TABLE IF NOT EXISTS ops.agent_logs (
    log_id         bigint       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at     timestamptz  NOT NULL DEFAULT now(),
    agent          text         NOT NULL,              -- 'forecast_analyst'
    model_id       text         NOT NULL,
    question       text         NOT NULL,
    answer         text,
    status         text         NOT NULL
                   CHECK (status IN ('answered', 'max_turns', 'stopped', 'error')),
    turns          integer      NOT NULL DEFAULT 0,    -- model calls in the loop
    tool_calls     jsonb        NOT NULL DEFAULT '[]', -- [{name, input, ok, error?}, ...]
    input_tokens   integer      NOT NULL DEFAULT 0,
    output_tokens  integer      NOT NULL DEFAULT 0,
    latency_ms     integer      NOT NULL DEFAULT 0,    -- wall clock for the whole loop
    error          text
);
