-- One row per deal with typed columns pulled out of the JSON payload.
-- Defensive on purpose: a bad amount becomes NULL instead of breaking the whole view.
DROP VIEW IF EXISTS staging.stg_deals CASCADE;

CREATE VIEW staging.stg_deals AS
SELECT
    d.hubspot_id                                            AS deal_id,
    p ->> 'dealname'                                        AS deal_name,
    p ->> 'rep_id'                                          AS rep_id,
    -- HubSpot sends amounts as text ("18000"). Only cast what looks like a number.
    CASE
        WHEN p ->> 'amount' ~ '^\s*-?\d+(\.\d+)?\s*$' THEN (p ->> 'amount')::numeric(14, 2)
    END                                                     AS amount,
    p ->> 'pipeline'                                        AS pipeline_id,
    p ->> 'dealstage'                                       AS stage_id,
    -- Dates are stored in UTC; convert to a plain date in UTC so quarters don't shift.
    ((p ->> 'createdate')::timestamptz AT TIME ZONE 'UTC')::date AS created_date,
    ((p ->> 'closedate')::timestamptz AT TIME ZONE 'UTC')::date  AS close_date,
    p ->> 'hs_next_step'                                    AS next_step,
    (p ->> 'hs_lastmodifieddate')::timestamptz              AS hubspot_updated_at,
    -- A deal can list the same company twice (labelled + unlabelled link); take the first.
    d.payload -> 'associations' -> 'companies' -> 'results' -> 0 ->> 'id' AS company_id,
    d.synced_at
FROM raw.hubspot_deals AS d
CROSS JOIN LATERAL (SELECT d.payload -> 'properties' AS p) AS props;
