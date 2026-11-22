-- One row per deal stage, unpacked from the pipeline JSON.
-- DROP ... CASCADE then CREATE: lets a view's columns change; `just migrate` recreates any
-- dependent views right after, because view files run in name order.
DROP VIEW IF EXISTS staging.stg_pipeline_stages CASCADE;

CREATE VIEW staging.stg_pipeline_stages AS
SELECT
    p.hubspot_id                                AS pipeline_id,
    p.payload ->> 'label'                       AS pipeline_label,
    s ->> 'id'                                  AS stage_id,
    s ->> 'label'                               AS stage_label,
    (s ->> 'displayOrder')::int                 AS display_order,
    (s -> 'metadata' ->> 'probability')::numeric AS probability,  -- 0.0 to 1.0
    (s -> 'metadata' ->> 'isClosed')::boolean   AS is_closed
FROM raw.hubspot_pipelines AS p
CROSS JOIN LATERAL jsonb_array_elements(p.payload -> 'stages') AS s;
