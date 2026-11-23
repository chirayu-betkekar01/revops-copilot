"""Apply every SQL file in sql/migrations, then every view in sql/views, in name order,
then (re)create the analyst agent's read-only login role.

    just migrate

Each file must be safe to re-run (CREATE TABLE IF NOT EXISTS, DROP VIEW ... CREATE VIEW),
so running this after every `just up` or every SQL change is harmless.
A heavier tool (Alembic) can replace this later.
"""

import os
from pathlib import Path
from typing import LiteralString

import psycopg
from psycopg import sql

# Tables first, then the views that read them.
SQL_DIRS = (Path("sql/migrations"), Path("sql/views"))

AGENT_ROLE = "agent_ro"
# Layered limits for the agent's own login. Even if every check in the Python code failed,
# this role can only read the marts schema, never write, and never run a query over 5 seconds.
AGENT_ROLE_SETUP: tuple[LiteralString, ...] = (
    "ALTER ROLE {role} SET default_transaction_read_only = on",
    "ALTER ROLE {role} SET statement_timeout = '5s'",
    "ALTER ROLE {role} SET search_path = marts",
)
# Views are dropped and recreated on every migrate, which drops their grants. So these run at
# the end of EVERY migrate() call (not only when the role is created): anything that recreates
# the views, tests included, also restores the agent's read access.
AGENT_GRANTS: tuple[LiteralString, ...] = (
    "GRANT USAGE ON SCHEMA marts TO {role}",
    "GRANT SELECT ON ALL TABLES IN SCHEMA marts TO {role}",
)


def migrate(conn: psycopg.Connection) -> list[str]:
    applied = []
    for directory in SQL_DIRS:
        for path in sorted(directory.glob("*.sql")):
            # No parameters, so psycopg sends the file as one script (multiple statements ok).
            conn.execute(path.read_text(encoding="utf-8"))
            applied.append(f"{directory.name}/{path.name}")
    if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (AGENT_ROLE,)).fetchone():
        apply_agent_grants(conn)
    return applied


def apply_agent_grants(conn: psycopg.Connection) -> None:
    for statement in AGENT_GRANTS:
        conn.execute(sql.SQL(statement).format(role=sql.Identifier(AGENT_ROLE)))


def ensure_agent_role(conn: psycopg.Connection, password: str) -> None:
    """Create or update the agent's login role and re-apply its grants and limits."""
    exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (AGENT_ROLE,)).fetchone()
    verb = sql.SQL("ALTER" if exists else "CREATE")
    # sql.Literal quotes the password safely; it never goes through string concatenation.
    conn.execute(
        sql.SQL("{} ROLE {} WITH LOGIN PASSWORD {}").format(
            verb, sql.Identifier(AGENT_ROLE), sql.Literal(password)
        )
    )
    for statement in AGENT_ROLE_SETUP:
        conn.execute(sql.SQL(statement).format(role=sql.Identifier(AGENT_ROLE)))
    apply_agent_grants(conn)


def main() -> None:
    # connect_timeout: if Docker/Postgres isn't running, fail in 5s instead of hanging forever.
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, connect_timeout=5) as conn:
        for name in migrate(conn):
            print(f"applied {name}")
        password = os.environ.get("AGENT_DB_PASSWORD", "")
        if password:
            ensure_agent_role(conn, password)
            print(f"role {AGENT_ROLE}: read-only on marts, 5s statement timeout")
        else:
            print(f"skipped role {AGENT_ROLE}: set AGENT_DB_PASSWORD in .env to create it")


if __name__ == "__main__":
    main()
