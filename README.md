# RevOps Copilot

Pulls deals from HubSpot, forecasts quarterly bookings, and models sales comp plans against
that forecast, with AI agents on Amazon Bedrock that answer questions in plain English.
Full spec: [docs/SCOPE.md](docs/SCOPE.md).

> MVP steps 1-7 are done. The analyst agent runs on Amazon Nova 2 Lite until the AWS account can
> use Anthropic models (one setting: `BEDROCK_MODEL_ID`).

## Quick start (Windows, PowerShell)

```powershell
Copy-Item .env.example .env   # then edit .env: password + HubSpot service key
uv sync                       # create .venv with all dependencies
just up                       # build images, start db + api + dashboard
just migrate                  # create tables and views
just seed                     # push synthetic deals into a HubSpot test account (once)
just sync                     # copy HubSpot into Postgres
just forecast                 # weighted pipeline forecast for this quarter
just comp                     # commission payouts for every rep and quarter
aws sso login --profile revops  # Bedrock access for the agent (see .env.example)
just ask "What's our forecast this quarter?"
just test                     # run the tests
```

Then open the dashboard. Every design decision is logged in
[docs/notes/decisions.md](docs/notes/decisions.md).

- API: http://localhost:8000/health (interactive docs at `/docs`)
- Dashboard: http://localhost:8501

Laptop-side ports come from `.env` (`POSTGRES_PORT`, `API_PORT`, `DASHBOARD_PORT`). Change them
there if another project already uses 5432, 8000 or 8501.

Run `just` on its own to list every command.

## What `docker-compose.yml` provides

The rest of the repo depends on these from it:

| Service | Must have |
| --- | --- |
| `db` | Image `postgres:16`. `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` from `.env`. Host port `${POSTGRES_PORT}` → container `5432`. A **named volume** on `/var/lib/postgresql/data`. `./sql/init` mounted to `/docker-entrypoint-initdb.d` (read-only is fine). A **healthcheck** (hint: `pg_isready`). |
| `api` | Built from context `.` with `app/Dockerfile`. Port `8000:8000`. `DATABASE_URL` pointing at host `db` (not `localhost`). Starts after `db`. |
| `dashboard` | Built from context `.` with `dashboard/Dockerfile`. Port `8501:8501`. `API_URL=http://api:8000`. Starts after `api`. |

Why the build context is `.` and not `./app`: both Dockerfiles need `pyproject.toml` and `uv.lock`
from the repo root.

## Layout

```
app/        FastAPI service (Step 1: /health only)
dashboard/  Streamlit app (Step 1: placeholder page)
sql/init/   SQL run once when Postgres starts on an empty volume
tests/      pytest
docs/       SCOPE.md and learning notes
```

Folders for later steps (`sync/`, `seed/`, `comp/`, `forecast/`, `agents/`) get created in the step
that first needs them. Git doesn't track empty folders.
