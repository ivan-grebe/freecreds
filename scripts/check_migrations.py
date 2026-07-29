"""Verify that every D1 migration applies cleanly in order."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from freecreds import db


def _schema_snapshot(connection: sqlite3.Connection) -> dict[str, dict[str, list[tuple]]]:
    tables = [
        row[0]
        for row in connection.execute(
            """SELECT name FROM sqlite_master
               WHERE type = 'table'
                 AND name NOT LIKE 'sqlite_%'
                 AND name != '_freecreds_migrations'
               ORDER BY name"""
        )
    ]
    snapshot = {}
    for table in tables:
        columns = [tuple(row[1:6]) for row in connection.execute(f'PRAGMA table_info("{table}")')]
        foreign_keys = [
            tuple(row[2:8]) for row in connection.execute(f'PRAGMA foreign_key_list("{table}")')
        ]
        indexes = []
        for index in connection.execute(f'PRAGMA index_list("{table}")'):
            index_name = index[1]
            index_columns = tuple(
                row[2] for row in connection.execute(f'PRAGMA index_info("{index_name}")')
            )
            indexes.append((index_name, bool(index[2]), index_columns))
        snapshot[table] = {
            "columns": columns,
            "foreign_keys": sorted(foreign_keys),
            "indexes": sorted(indexes),
        }
    return snapshot

def main() -> None:
    root = Path(__file__).resolve().parents[1]
    migrations = sorted((root / "migrations").glob("*.sql"))
    if not migrations:
        raise SystemExit("No migrations found")

    migration_db = sqlite3.connect(":memory:")
    try:
        for migration in migrations:
            migration_db.executescript(migration.read_text(encoding="utf-8"))
            print(f"applied {migration.name}")

        runtime_db = sqlite3.connect(":memory:")
        try:
            db.init_db(runtime_db)
            db.init_db(runtime_db)
            if _schema_snapshot(migration_db) != _schema_snapshot(runtime_db):
                raise SystemExit("Runtime SQLite schema does not match D1 migrations")
            print("runtime schema matches migrations")
        finally:
            runtime_db.close()
    finally:
        migration_db.close()


if __name__ == "__main__":
    main()
