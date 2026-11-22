-- Creates the five schemas from docs/SCOPE.md ("Data model").
--
-- Postgres runs every .sql file in /docker-entrypoint-initdb.d, in alphabetical order,
-- but ONLY the first time the container starts with an EMPTY data volume.
-- Edit this file later and nothing happens until you wipe the volume (docker compose down -v).

CREATE SCHEMA IF NOT EXISTS raw;      -- HubSpot JSON exactly as the API returned it
CREATE SCHEMA IF NOT EXISTS staging;  -- cleaned, typed versions of raw
CREATE SCHEMA IF NOT EXISTS marts;    -- tables the forecast, comp engine and agents read
CREATE SCHEMA IF NOT EXISTS comp;     -- comp plans, tiers, quotas, commission lines
CREATE SCHEMA IF NOT EXISTS ops;      -- sync runs and agent call logs
