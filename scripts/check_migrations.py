"""Verify that every D1 migration applies cleanly in order."""

from __future__ import annotations

import sqlite3
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    migrations = sorted((root / "migrations").glob("*.sql"))
    if not migrations:
        raise SystemExit("No migrations found")

    connection = sqlite3.connect(":memory:")
    try:
        for migration in migrations:
            connection.executescript(migration.read_text(encoding="utf-8"))
            print(f"applied {migration.name}")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
