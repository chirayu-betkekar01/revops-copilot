-- Save one HubSpot record into a raw table: insert it if new, overwrite it if we've seen it.
-- Originally a ✋ hand-written file; written by Claude at Chirayu's request (2026-11-22).
--
-- The sync code fills in:
--   {table}           the target table, e.g. raw.hubspot_deals (safely quoted)
--   %(hubspot_id)s    the record's HubSpot id (text)
--   %(payload)s       the whole record as JSON (jsonb)
--
-- ON CONFLICT (hubspot_id) is what makes the sync idempotent: the primary key on hubspot_id
-- means a second copy can't be inserted, so Postgres updates the existing row instead.
-- EXCLUDED.<column> = the value this statement just tried to insert.

INSERT INTO {table} (hubspot_id, payload, synced_at)
VALUES (%(hubspot_id)s, %(payload)s, now())
ON CONFLICT (hubspot_id) DO UPDATE
SET payload   = EXCLUDED.payload,
    synced_at = EXCLUDED.synced_at
