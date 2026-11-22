"""Unit tests for the API. No database needed: check_database is swapped out."""

import psycopg
import pytest
from fastapi.testclient import TestClient

from app import main

# Not using `with TestClient(...)`, so the startup DB check (lifespan) doesn't run.
client = TestClient(main.app)


def test_health_ok_when_database_up(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "check_database", lambda: None)

    resp = client.get("/health")

    assert resp.status_code == 200
    assert resp.json() == {"api": "ok", "database": "ok"}


def test_health_503_when_database_down(monkeypatch: pytest.MonkeyPatch) -> None:
    def db_down() -> None:
        raise psycopg.OperationalError("connection refused")

    monkeypatch.setattr(main, "check_database", db_down)

    resp = client.get("/health")

    assert resp.status_code == 503
    assert resp.json() == {"api": "ok", "database": "unreachable"}
