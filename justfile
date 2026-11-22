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

# Run the test suite
test:
    uv run pytest

# Lint, format check and type check
lint:
    uv run ruff check .
    uv run ruff format --check .
    uv run mypy app dashboard tests
