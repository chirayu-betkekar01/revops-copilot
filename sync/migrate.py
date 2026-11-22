"""Apply every SQL file in sql/migrations, in name order.

    just migrate

Each file must be safe to re-run (CREATE TABLE IF NOT EXISTS etc.), so running this
after every `just up` is harmless. A heavier tool (Alembic) can replace this later.
"""

import os
from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path("sql/migrations")


def migrate(conn: psycopg.Connection) -> list[str]:
    applied = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        # No parameters, so psycopg sends the file as one script (multiple statements are fine).
        conn.execute(path.read_text(encoding="utf-8"))
        applied.append(path.name)
    return applied


def main() -> None:
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        for name in migrate(conn):
            print(f"applied {name}")


if __name__ == "__main__":
    main()
