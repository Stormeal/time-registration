"""SQLite connection, transaction, and schema-migration support."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from importlib.resources import files
from pathlib import Path


class SQLiteDatabase:
    """Create configured short-lived SQLite connections."""

    def __init__(self, database_file: Path) -> None:
        self._database_file = database_file

    @property
    def database_file(self) -> Path:
        return self._database_file

    def initialize(self) -> None:
        """Create the database and apply every pending migration."""
        self._database_file.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            applied = {
                int(row["version"])
                for row in connection.execute("SELECT version FROM schema_migrations")
            }
            migration_root = files("qi_flow.infrastructure.sqlite.migrations")
            migrations = sorted(
                (item for item in migration_root.iterdir() if item.name.endswith(".sql")),
                key=lambda item: item.name,
            )
            for migration in migrations:
                version_text, _, migration_name = migration.name.partition("_")
                version = int(version_text)
                if version in applied:
                    continue
                sql = migration.read_text(encoding="utf-8")
                escaped_name = migration_name.replace("'", "''")
                script = (
                    "BEGIN IMMEDIATE;\n"
                    f"{sql}\n"
                    "INSERT INTO schema_migrations(version, name) "
                    f"VALUES ({version}, '{escaped_name}');\n"
                    "COMMIT;"
                )
                try:
                    connection.executescript(script)
                except sqlite3.Error:
                    connection.rollback()
                    raise

    def connect(self) -> sqlite3.Connection:
        """Open a connection configured for integrity and modest concurrency."""
        connection = sqlite3.connect(self._database_file, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Provide an explicit atomic write transaction."""
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
