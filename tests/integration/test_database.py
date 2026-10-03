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
    assert [row["version"] for row in migrations] == [1, 2, 3, 4, 5, 6]


def test_legacy_default_weekly_target_is_migrated_to_37_hours(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "qi-flow.sqlite3")
    database.initialize()
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO settings(key, value_json, updated_at_utc) VALUES (?, ?, ?)",
            ("default_weekly_target_minutes", "2100", "2026-09-15T00:00:00+00:00"),
        )
        connection.execute("DELETE FROM schema_migrations WHERE version=6")

    database.initialize()

    with closing(database.connect()) as connection:
        row = connection.execute(
            "SELECT value_json FROM settings WHERE key='default_weekly_target_minutes'"
        ).fetchone()
    assert row["value_json"] == "2220"


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


def test_unreadable_database_can_be_replaced_while_failure_traceback_is_retained(
    tmp_path: Path,
) -> None:
    database = SQLiteDatabase(tmp_path / "unreadable.sqlite3")
    database.database_file.write_bytes(b"unreadable synthetic database")
    with pytest.raises(sqlite3.DatabaseError) as failure:
        database.initialize()

    replacement = SQLiteDatabase(tmp_path / "verified.sqlite3")
    replacement.initialize()
    # Recovery runs inside the exception handler, so its traceback still owns local variables.
    assert failure.value.__traceback__ is not None
    replacement.database_file.replace(database.database_file)
    database.initialize()
    with closing(database.connect()) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert failure.value.__traceback__ is not None
