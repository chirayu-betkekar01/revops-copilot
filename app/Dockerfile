# Image for the FastAPI service. Build context is the repo root (see docker-compose.yml).
FROM python:3.12-slim

# Copy the uv binary from its official image, pinned to the version used locally.
COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /bin/uv

WORKDIR /code
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/code/.venv/bin:$PATH"

# Dependencies first. Docker caches this layer and only re-runs it when
# pyproject.toml or uv.lock change, so code edits rebuild in seconds.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --group api

# Code last, because it changes most often. The API runs the analyst agent, so it needs agents/.
COPY agents ./agents
COPY app ./app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
