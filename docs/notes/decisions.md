# Design decisions log

Every design choice made while building, with the alternatives and why. Kept so we can
discuss each one later (and so I can defend them in an interview). Newest step at the bottom.

Format: **Decision**: what we did. *Alternatives*: what else we could have done. *Why*: the reason.
*Revisit*: when it might change.

---

## Step 1: Repo and Docker Compose

1. **One `pyproject.toml` with dependency groups** (`api`, `dashboard`, `seed`, `sync`, `dev`).
   *Alternatives*: one flat dependency list; a separate project per service.
   *Why*: each Docker image installs only its group (the API image skips Streamlit's ~100 MB),
   while one lockfile keeps versions identical everywhere. *Revisit*: if services diverge a lot.

2. **Schemas created by `sql/init/00_schemas.sql`** (runs only on an empty volume).
   *Alternatives*: Alembic; a migrate script. *Why*: zero tooling for 5 `CREATE SCHEMA` lines.
   *Revisit*: superseded for tables by decision 23 (`just migrate`).

3. **API checks the database at startup and crashes if it's unreachable** (fail fast).
   *Alternatives*: start anyway and fail on first request; retry in a loop.
   *Why*: a broken service should fail loudly at boot, not look healthy. Made the
   `depends_on` race visible in the break-it.

4. **`/health` returns 503 (not a crash) when the DB goes down while running.**
   *Why*: the API can't fix the DB; staying up keeps the dashboard able to report the problem.
   Full error goes to logs, the response stays generic (no internals leaked).

5. **`depends_on: condition: service_healthy` + a `pg_isready` healthcheck.**
   *Alternatives*: plain `depends_on`; retry logic in the app. *Why*: "container started" isn't
   "database ready". Healthcheck lets Docker wait for ready.

6. **Laptop-side ports configurable in `.env`** (db 5433, api 8010 on this machine).
   *Why*: another local project already used 5432 and 8000. Inside Docker, services always
   talk on the default ports by service name (`db:5432`, `api:8000`).

7. **LF line endings enforced by `.gitattributes`.**
   *Why*: Windows CRLF breaks shell scripts and some files inside Linux containers.

8. **`just` recipes run in PowerShell** (`set windows-shell`), `.env` auto-loaded.
   *Why*: matches the native-Windows setup; no Git Bash dependency.

## Step 2: Seed HubSpot

9. **Seed through the HubSpot API, not straight into Postgres** (from SCOPE.md).
   *Why*: the Step 3 sync then gets tested against real paging, formats, ids and rate limits.

10. **Generator separated from the sender** (`seed/generator.py` vs `seed/seed_hubspot.py`,
    shared shapes in `seed/models.py`). *Why*: the generator is pure Python, testable without
    network; the sender knows nothing about business logic.

11. **Fixed `AS_OF` date (2026-11-22) and a seeded `random.Random`.**
    *Alternatives*: `date.today()`, global `random`. *Why*: same seed = same data, every day.

12. **Deal sizes tuned to the comp plan's $150k/quarter quota** (median deal ~$9.5k).
    *Alternatives*: bigger mid-market deals and raise the quota in SCOPE.md.
    *Why*: 2,000 deals / 8 reps / 6 quarters is ~11 wins per rep per quarter, so typical deals
    must be small for reps to land near 100% (they range 40-182%, exercising every tier).
    *Revisit*: if we want a classic enterprise motion, change quota and sizes together.

13. **Realism baked in on purpose**: 3 segments (size drives deal size, cycle and win rate),
    rep territories and skill spread, 30% expansions, quarter-end bunching, slips (more
    likely on lost deals), stale open deals past their close date, outcome-specific notes.
    *Why*: clean data teaches nothing; these patterns are what forecasting and risk models need.

14. **Outcome decided up front, even for open deals** (hidden "will win").
    *Why*: lets slips and notes correlate with the eventual outcome, like real warning signs.
    *Revisit*: it's ground truth we could use later to score the risk agent.

15. **Company domains use `.example`** (reserved, never a real site).
    *Why*: avoids accidentally pointing at real companies.

16. **Reps: stable `rep_id` + display name; `rep_id` is a HubSpot dropdown.**
    *Alternatives*: store names directly; plain text field. *Why*: names change and repeat;
    the comp engine needs a key that never changes. The dropdown shows names in HubSpot's UI.
    The `REPS` list becomes `dim_reps` in Step 4.

17. **Records linked by our own `seed_ref`, not by response order.**
    *Why*: HubSpot doesn't promise batch results come back in the order sent.

18. **Safety rails on the seed script**: dry-run mode; account preflight (stages, fields,
    industry values); write-permission probe (create + delete one throwaway record per type);
    duplicate guard on `seed_batch`; y/N confirmation.
    *Why*: a half-finished upload of thousands of records is painful to clean up. The probe
    exists because HubSpot's docs were unclear about the notes scope.

19. **Batch partial failures (HTTP 207) treated as errors.**
    *Why*: a 207 looks like success but means some records failed; silently continuing would
    leave a hole in the data.

20. **Retry only on 429, with exponential backoff + jitter, max 6 attempts; honour Retry-After.**
    *Alternatives*: fixed delay; retry all errors; a library like `tenacity`.
    *Why*: 429 means "rejected, not processed", so retrying a create is safe. Timeouts are not
    retried because a create might have succeeded (risk of duplicates). Jitter stops many
    threads retrying in lockstep. *Revisit*: add 502/503/504 retries for reads.

21. **Known gap: HubSpot stage history can't be backdated.** Deals were created directly in
    their final stage, so `propertiesWithHistory` won't show realistic stage paths.
    *Revisit*: weekend 2-3 (historical conversion, ML). Options: walk deals through stages via
    updates (real history, but all timestamped at seed time), or store the generated stage
    path in a custom property.

22. **Known gap: seeded notes aren't tagged with `seed_batch`.** Deleting seeded data later
    needs to go via the deals' note associations.

## Step 3: Sync into raw

23. **Tables created by idempotent SQL files + `just migrate`** (`CREATE TABLE IF NOT EXISTS`).
    *Alternatives*: wipe the volume on every change; Alembic now. *Why*: re-runnable, no data
    loss, no new tool yet. *Revisit*: Alembic with the API work (handles ALTERs and versions).

24. **ELT: raw tables store the full HubSpot JSON in a `payload` JSONB column.**
    *Alternatives*: parse into typed columns during the sync (ETL).
    *Why*: parsing bugs can be fixed by re-reading our copy, new fields are already there,
    and raw -> staging -> marts is the standard layering. Typed columns come as views in Step 4.

25. **Upsert keyed on HubSpot's id** (`INSERT ... ON CONFLICT (hubspot_id) DO UPDATE`).
    *Why*: makes the sync idempotent: re-running updates rows instead of duplicating them.

26. **One SQL template for all four raw tables**, table name injected with
    `psycopg.sql.Identifier`, values passed as parameters.
    *Alternatives*: one SQL file per table; f-strings. *Why*: one statement to maintain;
    `Identifier` quotes safely and parameters prevent SQL injection (no string concatenation).

27. **Commit per page of 100** (each page in its own transaction).
    *Alternatives*: one transaction for the whole run (all-or-nothing).
    *Why*: a crash keeps progress; the upsert makes re-running safe.

28. **After a crash, re-run the full sync instead of resuming from a saved cursor.**
    *Why*: simpler, and HubSpot cursors can expire. Upserts make it harmless.
    *Revisit*: incremental sync (weekend 2) uses a `hs_lastmodifieddate` watermark instead.

29. **`ops.sync_runs` logs every run**: start, finish, status (running/success/failed),
    row counts per object (JSONB), error. Ctrl+C is caught (`BaseException`) and logged as
    failed. *Known gap*: a hard kill leaves the row stuck at `running`.

30. **Synced objects: companies, deals, notes, deal pipelines. Not owners.**
    *Why*: the account's only owner is the real account holder (personal data we don't need);
    reps come from the synthetic `rep_id`.

31. **Explicit property lists per object**, and associations fetched one way only
    (deal -> company, note -> deal). *Why*: list endpoints return only defaults unless asked;
    one direction is enough to rebuild the links.

32. **Sync reuses `seed/hubspot_client.py`** (auth, paging helpers, 429 backoff).
    *Alternatives*: move it to a shared package now. *Why*: avoided touching Step 2 code.
    *Revisit*: move to a shared module when a third user appears.

33. **Known gap: deletions in HubSpot aren't removed from raw.** A full sync only upserts.
    *Revisit*: weekend 2 (fetch archived records, or mark rows not seen in a run).

34. **The idempotency test uses real Postgres with fake `test-` ids and cleans up after itself.**
    *Alternatives*: a separate test database. *Why*: no extra setup. *Revisit*: a dedicated
    test DB once tests get heavier.

## Step 4: Views and weighted forecast

35. **Layered views: `staging.stg_*` (typed columns from JSON) -> `marts.*` (business-ready).**
    `stg_pipeline_stages`, `stg_deals`, `stg_companies`; `marts.dim_reps`, `marts.fct_deals`.
    *Alternatives*: one big view; dbt now. *Why*: each layer has one job and can be tested on
    its own; this is the exact shape dbt will take over in weekend 2.

36. **View files are `DROP VIEW IF EXISTS ... CASCADE; CREATE VIEW`, applied by `just migrate`
    after the table migrations, in file-name order** (010, 011, 020...).
    *Alternatives*: `CREATE OR REPLACE VIEW` (can't drop or rename columns).
    *Why*: views can change shape freely; CASCADE drops dependents, which the later files
    immediately recreate. *Revisit*: dbt manages this from weekend 2.

37. **Defensive parsing in `stg_deals`**: an amount that isn't a plain number becomes NULL
    (regex check before the cast) instead of crashing the whole view.
    *Why*: one bad record in HubSpot shouldn't break every report.

38. **Dates converted to plain dates in UTC** (`AT TIME ZONE 'UTC'`).
    *Why*: HubSpot stores midnight UTC; converting in the server's time zone could push a
    deal into the previous day, and on the 1st, into the previous quarter.

39. **Won / lost / open come from the stage's metadata** (`isClosed` + probability 1 or 0),
    not hard-coded stage ids. *Why*: works for any pipeline, including new ones.

40. **LEFT JOINs in `fct_deals`; unknown stage = open at 0%.**
    *Why*: a deal with a stage or rep we can't match stays visible and gets flagged, instead of
    silently vanishing from totals (an INNER JOIN would drop it).

41. **Rep names come from HubSpot's `rep_id` dropdown options** (sync now also copies that
    property definition into `raw.hubspot_properties`).
    *Alternatives*: a hard-coded reps table in SQL; read `seed/models.py`.
    *Why*: one source of truth. Rename a rep in HubSpot and the next sync picks it up.
    Plan and quota columns join in from the comp schema in Step 5.

42. **Forecast = closed-won to date + sum(open amount x stage probability), current quarter.**
    Open deals still due in an *earlier* quarter are excluded and counted (`n_overdue_prior`);
    missing amounts count as $0 and unknown stages as 0%, both counted.
    *Why*: the number stays conservative and every data problem is visible next to it.

43. **Forecast maths lives in one SQL file; Python only runs it and stores the result** in
    `marts.fct_forecast_runs`, with the building blocks (closed won, open, weighted, counts).
    *Why*: every number shown anywhere traces back to a stored run; "why did it change since
    last week" becomes a comparison of two rows. Range columns (p10/p90) stay NULL until the
    Monte Carlo method in weekend 3.

44. **Forecast reads the current state of deals, so it's only valid for `as_of` = today.**
    *Why*: no stage history yet (see 21). *Revisit*: point-in-time rebuild for backtests.

45. **Forecast tests run inside a rolled-back transaction, with made-up deals in 2099 Q1.**
    *Why*: exact expected numbers, real data untouched, real 2025-2026 deals can't leak in.

46. **Finding: the weighted forecast says $2.19M for 2026 Q4, but past quarters booked about
    $1.0M each.** $1.46M sits in "Contract Sent" at HubSpot's default 90%. Two causes:
    (a) this is the known weakness of weighted pipeline: default stage probabilities are
    optimistic and nobody calibrates them; (b) a generator gap: open deals get their stage
    from how far through the sales cycle they are, *not* from whether they'll win, so
    late-stage deals here win only ~25% (real teams see far higher late-stage win rates).
    *Revisit*: make stage depend on outcome in the generator (needs a HubSpot re-seed), and
    replace default probabilities with historical conversion rates (weekend 2).
    *Update*: (b) fixed by decision 47. Forecast run 2 on the new data: **$1.63M**. By the
    generator's own funnel rates the true expectation is roughly $1.4M, so HubSpot's default
    percentages now overstate by ~15%: a realistic gap, which weekend 2's historical
    conversion method should close.

47. **Generator models a funnel: losing deals drop out at a stage; winners pass every stage.**
    `LOSS_STAGE_WEIGHTS = [0.30, 0.25, 0.20, 0.15, 0.10]` picks the stage a loser dies in; an
    open deal's stage is its time-progress stage, capped at the furthest stage it will reach;
    a lost deal's close date is when it died (inside that stage).
    *Alternatives*: simulate each stage-to-stage step with its own conversion rate (same idea,
    more code); keep time-only stages (unrealistic: every stage won ~25%).
    *Why*: real pipelines thin out stage by stage. Win rate given the stage reached is now
    about 25% -> 32% -> 43% -> 57% -> 77%, so stage carries real signal for forecasting.
    *Side effect*: rep attainment (2026 Q1-Q3) now spans 27%-252%, median 72%.
    *Revisit*: Step 5, if the comp plan should see more reps near 100%.

48. **Re-seeded as a new batch (`seed-v2`); the old batch was archived, not hard-deleted.**
    `just seed-archive <batch>` finds records by `seed_batch`, finds the notes through the
    deals' associations (works around gap 22), shows counts, asks, then archives notes ->
    deals -> companies. *Why*: archive = HubSpot's recycle bin (restorable ~90 days), the
    least destructive option; a new batch name keeps old and new data distinguishable.

49. **`just sync --full-refresh` empties the raw tables before syncing.**
    *Why*: a normal sync only upserts, so archived/deleted HubSpot records would linger in
    raw (gap 33). *Trade-off*: not atomic: if the sync then fails, raw stays partly empty until
    the next run. Fine for a local copy that HubSpot can always rebuild.

50. **Forecast run 1 (computed on the old v1 data) is kept in `marts.fct_forecast_runs`.**
    *Why*: stored runs are history; deleting one to make the record look tidy defeats the
    point of storing them.
