"""Derived backup scan results expire when files, folders or restored copies change."""

from contextlib import closing
from datetime import UTC, datetime, timedelta
from shutil import copyfile

from qi_flow.infrastructure.backups import BackupManager
from qi_flow.infrastructure.sqlite.database import SQLiteDatabase
from qi_flow.infrastructure.sqlite.repositories import SQLiteUnitOfWork


class Clock:
    value = datetime(2026, 10, 3, 12, tzinfo=UTC)

    def now(self):
        return self.value


def test_backup_scan_reuses_derived_validity_and_invalidates_after_changes(tmp_path, monkeypatch):
    database = SQLiteDatabase(tmp_path / "live.sqlite3")
    database.initialize()
    clock = Clock()
    manager = BackupManager(
        database, tmp_path / "backups", lambda: SQLiteUnitOfWork(database), clock
    )
    manager.ensure_daily_backup()
    checks = []
    original = manager._is_valid_database

    def checked(path, *args, **kwargs):
        checks.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(BackupManager, "_is_valid_database", staticmethod(checked))
    first = manager.list_backups()
    assert len(first) == 1
    checks.clear()
    assert manager.status().latest_backup == first[0]
    assert manager.list_backups() == first
    assert checks == []
    # A newly created daily copy must appear; the source date alone is not a cache key.
    clock.value += timedelta(days=1)
    manager.ensure_daily_backup()
    assert len(manager.list_backups()) == 2
    # Modification of an existing copy invalidates its prior successful integrity result.
    first[0].path.write_bytes(b"corrupt replacement")
    assert first[0] not in manager.list_backups()
    # Replacing a corrupt copy from a restored database is also observed.
    with closing(database.connect()) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    copyfile(database.database_file, first[0].path)
    assert len(manager.list_backups()) == 2
    # A different folder, additions and removals have independent catalog identities.
    other = tmp_path / "other"
    other.mkdir()
    replacement = other / "qi-flow-backup-restored.sqlite3"
    copyfile(database.database_file, replacement)
    assert [item.path for item in manager.list_backups(other)] == [replacement]
    replacement.unlink()
    assert manager.list_backups(other) == []
    assert len(manager.list_backups()) == 2
