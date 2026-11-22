# CLAUDE.md: RevOps Copilot

Read this file and `docs/SCOPE.md` before doing anything. SCOPE.md is the full spec (data model, forecasting method, comp rules, agents, AWS plan, step-by-step MVP guide). This file is how to work with me.

## What this project is

A portfolio project that pulls deals from HubSpot, forecasts quarterly bookings, and models sales compensation plans against that forecast, with AI agents on Amazon Bedrock that answer questions in plain English. It is meant to prove backend, data engineering and AI engineering skills on one real business problem.

## Who I am and how to work with me

- I'm Chirayu. I'm technical (Python, APIs, JSON, Sheets automation, n8n) and I'm using this project to level up in backend, data engineering and AI engineering.
- **Learning comes first. A working repo I can't explain is a failure.**
- I'm on **native Windows** (PowerShell, not WSL). Give PowerShell commands. Use `curl.exe` (not `curl`) and `just` (not `make`). Watch for path and line-ending issues.

### Rules for every step (follow these strictly)

1. **One step at a time.** Build only the step I name. Don't touch other modules or jump ahead.
2. **Explain before coding.** Describe the design, the alternatives and the trade-offs in a few bullets, then wait for my go-ahead before writing code.
3. **Respect hand-written files.** Files marked ✋ in SCOPE.md are written by me. You may review them and suggest changes with reasons, but never write or edit them unless I explicitly ask. Current ✋ list:
   - `docker-compose.yml` (Step 1)
   - ~~Data generator logic in `seed/generator.py` (Step 2)~~ written by Claude at my request
   - ~~Upsert SQL in the sync (Step 3)~~ written by Claude at my request
   - Forecast SQL query (Step 4)
   - Entire comp engine `comp/engine.py` (Step 5)
   - Agent loop in `agents/analyst.py` (Step 6)
4. **Keep changes small and reviewable.** I read every line before committing.
5. **When I ask "why?", teach, don't just fix.** Point to the concept and the official docs.
6. **End each step with:** a summary of what changed, the break-it exercise from SCOPE.md, and 2-3 self-check questions for me to answer.
7. **Never invent numbers.** Forecasts and payouts come from deterministic code; agents call tools for every figure.

**Current mode (from 2026-11-22): build first, learn later.** Claude builds each step end to end, including ✋ files, without stopping for go-aheads on routine design. Every design decision (what, alternatives, why, when to revisit) goes into `docs/notes/decisions.md` so we can review them all later.

## Current status

- [x] Step 0: accounts and tools. HubSpot developer test account + service key (rotated after exposure, stored outside the repo). Git, uv, just, Docker Desktop, Claude Code installed on Windows. AWS account setup deferred until Step 6.
- [x] Step 1: repo + Docker Compose. db + api + dashboard run via `just up`; API waits for the db healthcheck (`condition: service_healthy`). `docker-compose.yml` was written by Claude at my request; I did the break-it fix. Local ports: db 5433, api 8010 (5432 and 8000 are used by another project).
- [x] Step 2: seed HubSpot. 300 companies, 2,000 deals and ~6,400 notes uploaded (`seed_batch=seed-v1`); 8 named reps via the `rep_id` dropdown (stores id, shows name). Generator and 429 backoff written by Claude at my request. Known gap: HubSpot stage history can't be backdated (matters for weekend 2-3 forecasting).
- [x] Step 3: sync into raw. `just migrate` creates raw.hubspot_{companies,deals,notes,pipelines} (JSONB payloads) + ops.sync_runs; `just sync` does a full paged sync with per-page commits and upserts. Verified: two real syncs give exactly 300 / 2,000 / 6,456 / 1 rows.
- [ ] **Step 4: views + weighted forecast. NEXT.**
- [ ] Steps 5-7: see SCOPE.md "MVP build guide".

Update this section when a step is finished.

## Decisions made

| Area | Decision |
| --- | --- |
| Dev environment | Native Windows + Docker Desktop. Build everything locally first. |
| Language and tooling | Python 3.12, uv, Ruff, mypy, pytest |
| Database | Postgres 16 in Docker (RDS later). Schemas: raw, staging, marts, comp, ops |
| Transform | Plain SQL views in MVP, dbt Core from weekend 2 |
| Orchestration | Prefect locally; EventBridge Scheduler + ECS Fargate on AWS |
| API | FastAPI + Pydantic + SQLAlchemy + Alembic |
| UI | Streamlit |
| HubSpot auth | Service key (legacy private apps can no longer be created) |
| HubSpot owners | Owners are real users and can't be created via API. Use a custom deal property `rep_id` for 8 synthetic reps, mapped in `dim_reps` |
| LLM | Claude on Amazon Bedrock (Converse API). Sonnet for agents, Haiku for bulk scoring |
| Agents | Forecast analyst = hand-written Converse tool-calling loop. Deal risk + comp agents = Strands Agents |
| Later Bedrock services | Knowledge Bases on S3 Vectors, Guardrails, batch inference, AgentCore Runtime/Gateway/Memory (weekend 3-4) |
| Comp math | Pure Python with `Decimal`, plans in YAML. LLM never does the math |
| Forecast | MVP: weighted pipeline. v1: historical conversion + LightGBM win probability + Monte Carlo P10/P50/P90 |
| Deployment | Weekend 4: Terraform, RDS, Lambda + API Gateway (Mangum), Fargate, AgentCore, Secrets Manager, GitHub Actions with OIDC. No NAT gateway. $25 budget alert |

## Repo layout (target)

```
app/          FastAPI service
dashboard/    Streamlit app
sync/         HubSpot sync jobs
seed/         seed_hubspot.py (synthetic data pushed through the HubSpot API)
comp/         comp engine + plans/*.yaml
forecast/     forecasting code
agents/       Bedrock agents + tools
sql/          views, migrations
tests/        pytest, incl. agent evals later
docs/         SCOPE.md, notes/ (my learning log, predictions per step)
```

## Commands (fill in as they're created)

```powershell
just up        # docker compose up -d --build
just down      # docker compose down
just logs      # docker compose logs -f
just psql      # open psql in the db container
just test      # uv run pytest
just lint      # ruff check + ruff format --check + mypy
just seed-dry  # generate + check fake data, send nothing
just seed      # push fake data to HubSpot (asks first)
just migrate   # create/update tables from sql/migrations (safe to re-run)
just sync      # full sync HubSpot -> raw.* tables, logged in ops.sync_runs
```

## Security rules

- Never commit `.env`, tokens or keys. `.env` is gitignored; `.env.example` holds placeholder values only.
- Never print, log or echo the HubSpot service key or AWS credentials.
- Agents get a read-only Postgres role, SELECT-only SQL (validated with sqlglot), a forced LIMIT and a statement timeout.
- Synthetic data only. No real employee pay data, ever.

## Conventions

- Type hints everywhere; Ruff for lint and format.
- Money is `Decimal`, never `float`.
- SQL in `.sql` files or clearly named constants, not built with string concatenation.
- Small commits with descriptive messages.
- Line endings: LF (enforced by `.gitattributes`); git `core.autocrlf=input`.
