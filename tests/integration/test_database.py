"""Integration tests for SQLite initialization and transaction semantics."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from qi_flow.infrastructure.sqlite.database import SQLiteDatabase


def test_initialize_applies_initial_schema(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "qi-flow.sqlite3")

    database.initialize()

    with closing(database.connect()) as connection:
        tables = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        migrations = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()

    assert {
        "work_sessions",
        "deductions",
        "day_details",
        "weekly_targets",
        "settings",
        "audit_entries",
    } <= tables
    assert [row["version"] for row in migrations] == [1, 2, 3, 4, 5]


def test_transaction_rolls_back_on_failure(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "qi-flow.sqlite3")
    database.initialize()

    with (
        pytest.raises(RuntimeError, match="force rollback"),
        database.transaction() as connection,
    ):
        connection.execute(
            """
            INSERT INTO settings(key, value_json, updated_at_utc)
            VALUES ('theme', '"system"', '2026-09-15T00:00:00+00:00')
            """
        )
        raise RuntimeError("force rollback")

    with closing(database.connect()) as connection:
        count = connection.execute("SELECT COUNT(*) FROM settings").fetchone()[0]

    assert count == 0


def test_database_prevents_two_active_sessions(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "qi-flow.sqlite3")
    database.initialize()
    insert = """
        INSERT INTO work_sessions(
            id, actual_started_at_utc, source, created_at_utc, updated_at_utc
        ) VALUES (?, ?, 'timer', ?, ?)
    """
    timestamp = "2026-09-15T06:00:00+00:00"

    with database.transaction() as connection:
        connection.execute(insert, ("first", timestamp, timestamp, timestamp))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(insert, ("second", timestamp, timestamp, timestamp))
