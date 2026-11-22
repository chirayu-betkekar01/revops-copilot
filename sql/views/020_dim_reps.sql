-- One row per sales rep, read from the options of HubSpot's rep_id dropdown
-- (value = stable rep_id, label = display name).
-- Comp-related columns (plan, quota) join in from the comp schema in Step 5.
DROP VIEW IF EXISTS marts.dim_reps CASCADE;

CREATE VIEW marts.dim_reps AS
SELECT
    o ->> 'value'                                    AS rep_id,
    o ->> 'label'                                    AS rep_name,
    (o ->> 'displayOrder')::int                      AS display_order,
    NOT COALESCE((o ->> 'hidden')::boolean, false)   AS is_active
FROM raw.hubspot_properties AS prop
CROSS JOIN LATERAL jsonb_array_elements(prop.payload -> 'options') AS o
WHERE prop.hubspot_id = 'deals.rep_id';
