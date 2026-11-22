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

## Step 5: Comp engine

51. **Plans are YAML data with versions; a version is never edited, a new one is added**
    (`comp/plans/standard_ae.yaml`, each version has `effective_from`).
    *Alternatives*: plans in Python code; plans in database tables.
    *Why*: finance can read and change a YAML file; old versions stay intact, so any past
    payout can be reproduced exactly.

52. **Money and rates in YAML are quoted strings; an unquoted decimal is rejected.**
    *Why*: YAML turns `1.5` into a float, and floats can't hold most decimals exactly (0.1).
    Refusing floats at load time keeps Decimal exact from file to payout.

53. **Base rate and quarterly quota are derived, not stored** (variable / annual quota = 12.5%;
    quota / 4 = $150k). *Why*: one source for each number; they can't drift apart.

54. **Deals fill the tiers in close-date order (ties broken by deal id); a deal that crosses a
    tier boundary is split into one commission line per tier.**
    *Alternatives*: compute the total with the formula and give every deal a blended rate;
    "retroactive" accelerators (all bookings paid at the highest tier reached).
    *Why*: each dollar is paid at the rate of the tier it actually landed in, and every line
    points at one deal, so a rep can see exactly why each deal paid what it did.
    Retroactive plans exist in the real world, but they're a different plan type.

55. **Commission line grain: run x deal x tier** (SCOPE.md said rep x deal x period).
    *Why*: the split deals need one line per tier to stay traceable. 472 won deals produced
    490 lines (18 split).

56. **Mid-quarter plan change: each deal is paid under the plan version in force on its close
    date.** Its tier position is measured against that version's quota. Attainment is
    reported against the quota in force on the quarter's last day.
    *Alternatives*: pro-rate by days; pay the whole quarter under the start or end version.
    *Why*: simple to explain ("your deal closed on the 20th, the new plan was live").
    *Revisit*: if quota changes mid-quarter, tier boundaries jump; check with finance.

57. **Rounding: each line rounds half-up to the cent; the rep's payout is the sum of rounded
    lines.** *Why*: what's paid always equals the lines on the statement. It can differ from
    SCOPE.md's one-shot formula by at most a cent per line; tests check that bound.

58. **Each quarter starts from zero (no annual true-up or carry-over).**
    *Revisit*: annual plans, carry-forward of over-attainment, draws (SCOPE.md stretch).

59. **Every active rep gets a row for every quarter, including $0 quarters.**
    *Why*: a rep who booked nothing must show 0%, not disappear from the report.

60. **Each `just comp` stores a complete run in one transaction**: plan snapshot
    (`comp.comp_plans`), quotas, `comp.commission_runs`, `comp.rep_quarter_results`,
    `comp.commission_lines`. Re-running adds a new run instead of overwriting.
    *Why*: a run is all-or-nothing, and history shows how payouts changed. Readers (API,
    agents) must pick the latest run (or a specific one).

61. **Credited deals: won, with an amount and a rep, closed on or before `as_of`.** Zero or
    negative amounts aren't paid (no clawbacks yet); won deals of reps not on the plan are
    reported, not paid. *Revisit*: clawbacks and splits are SCOPE.md stretch items.

62. **Kept SCOPE.md's $150k quarterly quota although only 0-5 of 8 reps hit it per quarter**
    (2026 Q3: one rep at 214%, the rest 33-97%). *Why*: realistic (many teams see about half
    their reps at quota), and the spread exercises every tier. Cost of sales is 12.5-15%.
    *Revisit*: the weekend 3 scenario work ("quota +10%, tier 2 at 1.75x") changes this anyway.

63. **The engine is pure: no database, no network, no clock.** `comp/run.py` does the I/O.
    *Why*: SCOPE.md's rule "payouts are a pure function of (plan, quota, credited deals)";
    makes the 21 engine tests fast and exact, and lets the comp agent call it as a tool later.

## Step 7: API and dashboard (built before Step 6, which needs AWS)

64. **The API is read-only and serves stored runs only.** It never computes a forecast or a
    payout; it returns the latest (or a chosen) run from `marts.fct_forecast_runs` and the
    comp schema. *Why*: one place does each calculation (`just forecast`, `just comp`), so the
    API, dashboard and future agent can never disagree about a number.

65. **Every API database session is READ ONLY at the database level**
    (`default_transaction_read_only=on` on the pool). *Why*: defence in depth: even a bug or
    an injected query can't write. *Revisit*: a dedicated read-only Postgres role (Step 6
    needs one for the agent anyway).

66. **`psycopg_pool` for connections instead of SQLAlchemy** (CLAUDE.md's stack table says
    SQLAlchemy + Alembic). *Why*: the API only runs a handful of hand-written SELECTs; a pool
    is all it needs, and SQL stays visible in `app/queries.py`.
    *Revisit*: when the API gains write endpoints or migrations move to Alembic.

67. **Money travels as JSON strings** (`"18750.00"`, Pydantic's default for Decimal).
    *Why*: JSON numbers are floats in most clients; strings keep every cent exact.

68. **Inputs validated by Pydantic: `quarter` must be a quarter's first day; `limit` is 1-100;
    a chat question is 1-2,000 characters.** Bad input -> 422 with a message saying what's
    wrong (e.g. "must be the first day of a quarter, e.g. 2026-07-01").

69. **Database trouble mid-request returns 503 "Database unavailable", not a bare 500.**
    *Why*: the client (dashboard) can tell "the database is down" from "the API has a bug".

70. **`POST /agent/chat` exists now as a validated placeholder returning 501.**
    *Why*: the dashboard's chat box is wired end to end; Step 6 only swaps the inside.

71. **The dashboard talks only to the API, never the database.** GETs are cached for 30s.
    It checks `/health` first and stops with a clear message if the API or database is down;
    an API error further down the page is shown as an error, not a crash.

72. **Dashboard uses number tiles and tables, no charts.** *Why*: the content is a few headline
    numbers and one row per rep; tables keep exact values readable. The only graphic is an
    attainment bar per rep (display-only float; money is formatted from Decimal strings).
    *Known nit*: the bar uses Streamlit's default red accent, which can read as "bad".
    *Revisit*: a Streamlit theme, and a forecast trend chart once there are many runs.

73. **The forecast tile shows the change vs the previous run for the same quarter.**
    *Why*: "why did the number move?" is the question stored runs exist to answer.

74. **Chat box sits inline at the end of the page, fixed height.** *Why*: Streamlit pins a
    top-level chat input over the bottom of the window, and in 1.67 its default height
    rendered ~180px tall, covering the payout table.

75. **Bug caught by the dashboard test: negative money read "$-562,950" and Streamlit showed
    a green up arrow for a drop** (it reads a leading minus to pick the arrow). Fixed to
    "-$562,950"; the test now asserts the arrow points down.

76. **Testing: API endpoint tests swap the DB dependency for a connection inside a rolled-back
    transaction (made-up 2099 runs); dashboard tests use Streamlit's AppTest with a fake
    `httpx`** (API down, DB down, mid-page error, full render, chat).
