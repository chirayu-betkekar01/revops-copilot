"""Integration test: needs the Postgres container running (`just up`)."""

import os

import psycopg
import pytest

EXPECTED_SCHEMAS = {"raw", "staging", "marts", "comp", "ops"}


@pytest.mark.skipif("DATABASE_URL" not in os.environ, reason="DATABASE_URL not set (no .env?)")
def test_all_schemas_exist() -> None:
    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=3) as conn:
        rows = conn.execute("SELECT schema_name FROM information_schema.schemata").fetchall()

    found = {row[0] for row in rows}
    missing = EXPECTED_SCHEMAS - found
    assert not missing, f"Missing schemas: {missing}. Did sql/init run on an empty volume?"
