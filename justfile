# Shortcut commands. Run `just` on its own to list them.

# Run recipes in PowerShell on Windows (just defaults to `sh`, which needs Git Bash).
set windows-shell := ["powershell.exe", "-NoLogo", "-NoProfile", "-Command"]

# Load .env so recipes and tests see DATABASE_URL etc.
set dotenv-load

# List all recipes
default:
    @just --list

# Build images and start all containers in the background
up:
    docker compose up -d --build

# Stop and remove containers (the data volume is kept)
down:
    docker compose down

# Follow logs from all containers (Ctrl+C to stop)
logs:
    docker compose logs -f

# Open psql inside the db container (uses the container's own POSTGRES_* variables)
psql:
    docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'

# Generate fake data and check it, without touching HubSpot
seed-dry:
    uv run python -m seed.seed_hubspot --dry-run

# Push fake data into your HubSpot test account (asks before creating anything)
seed:
    uv run python -m seed.seed_hubspot

# Archive one seed batch in HubSpot (recycle bin, restorable ~90 days). Asks first.
seed-archive BATCH:
    uv run python -m seed.archive_seed {{BATCH}}

# Create/update database tables (safe to run any time)
migrate:
    uv run python -m sync.migrate

# Copy everything from HubSpot into the raw.* tables (add --full-refresh to empty them first)
sync *ARGS:
    uv run python -m sync.hubspot {{ARGS}}

# Weighted pipeline forecast for the current quarter, saved to marts.fct_forecast_runs
forecast *ARGS:
    uv run python -m forecast.weighted {{ARGS}}

# Step 2 break-it: hammer HubSpot (read-only) until it answers 429
rate-limit-demo:
    uv run python -m seed.rate_limit_demo

# Run the test suite
test:
    uv run pytest

# Lint, format check and type check
lint:
    uv run ruff check .
    uv run ruff format --check .
    uv run mypy app dashboard seed sync forecast tests
