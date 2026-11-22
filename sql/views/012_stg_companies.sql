-- One row per company with typed columns.
DROP VIEW IF EXISTS staging.stg_companies CASCADE;

CREATE VIEW staging.stg_companies AS
SELECT
    c.hubspot_id                                     AS company_id,
    p ->> 'name'                                     AS company_name,
    p ->> 'domain'                                   AS domain,
    p ->> 'industry'                                 AS industry,
    CASE WHEN p ->> 'numberofemployees' ~ '^\d+$'
         THEN (p ->> 'numberofemployees')::int END   AS employees,
    CASE WHEN p ->> 'annualrevenue' ~ '^\d+(\.\d+)?$'
         THEN (p ->> 'annualrevenue')::numeric(16, 2) END AS annual_revenue,
    c.synced_at
FROM raw.hubspot_companies AS c
CROSS JOIN LATERAL (SELECT c.payload -> 'properties' AS p) AS props;
