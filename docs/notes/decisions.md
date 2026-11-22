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
