"""Apply every SQL file in sql/migrations, then every view in sql/views, in name order.

    just migrate

Each file must be safe to re-run (CREATE TABLE IF NOT EXISTS, DROP VIEW ... CREATE VIEW),
so running this after every `just up` or every SQL change is harmless.
A heavier tool (Alembic) can replace this later.
"""

import os
from pathlib import Path

import psycopg

# Tables first, then the views that read them.
SQL_DIRS = (Path("sql/migrations"), Path("sql/views"))


def migrate(conn: psycopg.Connection) -> list[str]:
    applied = []
    for directory in SQL_DIRS:
        for path in sorted(directory.glob("*.sql")):
            # No parameters, so psycopg sends the file as one script (multiple statements ok).
            conn.execute(path.read_text(encoding="utf-8"))
            applied.append(f"{directory.name}/{path.name}")
    return applied


def main() -> None:
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        for name in migrate(conn):
            print(f"applied {name}")


if __name__ == "__main__":
    main()
