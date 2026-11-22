-- One row per deal: what the forecast, comp engine and agents read.
-- LEFT JOINs on purpose: a deal with an unknown stage or rep still shows up (flagged),
-- instead of silently disappearing from every total.
DROP VIEW IF EXISTS marts.fct_deals CASCADE;

CREATE VIEW marts.fct_deals AS
SELECT
    d.deal_id,
    d.deal_name,
    d.rep_id,
    r.rep_name,
    d.company_id,
    c.company_name,
    d.amount,
    d.pipeline_id,
    d.stage_id,
    s.stage_label,
    s.probability,
    s.stage_id IS NOT NULL                                AS is_known_stage,
    COALESCE(s.is_closed AND s.probability = 1, false)    AS is_won,
    COALESCE(s.is_closed AND s.probability = 0, false)    AS is_lost,
    NOT COALESCE(s.is_closed, false)                      AS is_open,  -- unknown stage = open
    d.created_date,
    d.close_date,
    date_trunc('quarter', d.close_date)::date             AS close_quarter,
    -- Closed deals: created -> closed. Open deals: created -> today.
    CASE WHEN COALESCE(s.is_closed, false)
         THEN d.close_date - d.created_date
         ELSE current_date - d.created_date
    END                                                   AS age_days,
    d.next_step
FROM staging.stg_deals AS d
LEFT JOIN staging.stg_pipeline_stages AS s
       ON s.pipeline_id = d.pipeline_id AND s.stage_id = d.stage_id
LEFT JOIN marts.dim_reps AS r ON r.rep_id = d.rep_id
LEFT JOIN staging.stg_companies AS c ON c.company_id = d.company_id;
