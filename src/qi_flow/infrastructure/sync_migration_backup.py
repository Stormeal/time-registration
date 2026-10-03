"""Verified local migration safety copies, kept outside daily backup retention."""

import os
import sqlite3
import tempfile
from pathlib import Path

from qi_flow.application.sync_migration import LocalSnapshot, local_snapshot
from qi_flow.infrastructure.backups import BackupManager
from qi_flow.infrastructure.sqlite.database import SQLiteDatabase
from qi_flow.infrastructure.sqlite.repositories import SQLiteUnitOfWork


class SQLiteMigrationSafety:
    def __init__(self, database: SQLiteDatabase, folder: Path) -> None:
        self._database, self._folder = database, folder

    def create_snapshot(self) -> LocalSnapshot:
        self._folder.mkdir(parents=True, exist_ok=True)
        descriptor, filename = tempfile.mkstemp(
            prefix="qi-flow-safety-before-sync-", suffix=".sqlite3", dir=self._folder
        )
        os.close(descriptor)
        destination = Path(filename)
        try:
            BackupManager._copy_database(self._database.database_file, destination)
            if not BackupManager._is_valid_database(destination):
                raise sqlite3.DatabaseError("The migration safety copy failed verification.")
            with SQLiteUnitOfWork(SQLiteDatabase(destination)) as uow:
                records = local_snapshot(uow)
            return LocalSnapshot(str(destination), records)
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
