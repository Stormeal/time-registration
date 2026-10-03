"""Consistent SQLite backup and restoration support."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from collections.abc import Callable
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from time import monotonic

from qi_flow.application.backups import BackupStatusView as BackupStatusView
from qi_flow.application.backups import BackupView as BackupView
from qi_flow.application.ports import Clock, UnitOfWork
from qi_flow.domain.time_rules import COPENHAGEN
from qi_flow.infrastructure.sqlite.database import SQLiteDatabase


class BackupManager:
    """Own daily backups, retention, validation, and guarded restoration."""

    _FOLDER_KEY = "backup_folder"
    _LAST_SUCCESS_KEY = "backup_last_success_date"
    _LAST_FOLDER_KEY = "backup_last_success_folder"
    _WARNING_KEY = "backup_warning"
    _BACKUP_PREFIX = "qi-flow-backup-"

    def __init__(
        self,
        database: SQLiteDatabase,
        default_folder: Path,
        uow_factory: Callable[[], UnitOfWork],
        clock: Clock,
    ) -> None:
        self._database = database
        self._default_folder = default_folder
        self._uow_factory = uow_factory
        self._clock = clock

    def status(self) -> BackupStatusView:
        with self._uow_factory() as uow:
            folder = self._folder_from_value(uow.settings.get(self._FOLDER_KEY))
            warning = uow.settings.get(self._WARNING_KEY)
        return BackupStatusView(
            folder=folder,
            warning=warning if isinstance(warning, str) and warning else None,
            latest_backup=next(iter(self.list_backups(folder)), None),
        )

    def set_folder(self, folder: Path) -> BackupStatusView:
        resolved = folder.expanduser().resolve()
        resolved.mkdir(parents=True, exist_ok=True)
        with self._uow_factory() as uow:
            uow.settings.save(self._FOLDER_KEY, str(resolved), self._now())
        return self.status()

    def ensure_daily_backup(
        self,
        *,
        destination: Path | None = None,
        work_date: date | None = None,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> BackupView | None:
        """Create today's backup once, or retry if the previous attempt failed."""
        today = (work_date or self._now().astimezone(COPENHAGEN).date()).isoformat()
        folder = (destination or self.status_folder()).expanduser().resolve()
        identity = os.path.normcase(str(folder))
        with self._uow_factory() as uow:
            last_success = uow.settings.get(self._LAST_SUCCESS_KEY)
            last_folder = uow.settings.get(self._LAST_FOLDER_KEY)
            warning = uow.settings.get(self._WARNING_KEY)
        if last_success == today and last_folder == identity and not warning:
            return None
        try:
            backup = self._create_daily_backup(today, folder, cancelled=cancelled)
        except (OSError, sqlite3.Error) as error:
            if self.destination_identity() == identity:
                self._set_warning(f"Backup failed: {error}")
            return None
        if (
            self.destination_identity() == identity
            and self._now().astimezone(COPENHAGEN).date().isoformat() == today
        ):
            self._clear_warning(today, identity)
        return backup

    def list_backups(self, folder: Path | None = None) -> list[BackupView]:
        target = folder or self.status_folder()
        return self.valid_backups_in(target)

    @classmethod
    def valid_backups_in(cls, target: Path) -> list[BackupView]:
        if not target.is_dir():
            return []
        backups: list[BackupView] = []
        for path in target.glob(f"{cls._BACKUP_PREFIX}*.sqlite3"):
            if cls._is_valid_database(path):
                backups.append(
                    BackupView(
                        path=path,
                        created_at=datetime.fromtimestamp(path.stat().st_mtime, COPENHAGEN),
                    )
                )
        return sorted(backups, key=lambda backup: backup.created_at, reverse=True)

    @classmethod
    def replace_unreadable_database(cls, database_file: Path, backup: BackupView) -> None:
        """Replace an unreadable live database only after the caller has confirmed it."""
        if not cls._is_valid_database(backup.path):
            raise ValueError("The selected backup is no longer a valid SQLite database.")
        staged = database_file.with_suffix(".recovery.sqlite3")
        try:
            cls._copy_database(backup.path, staged)
            if not cls._is_valid_database(staged):
                raise ValueError("The selected backup could not be verified for recovery.")
            database_file.with_name(f"{database_file.name}-wal").unlink(missing_ok=True)
            database_file.with_name(f"{database_file.name}-shm").unlink(missing_ok=True)
            os.replace(staged, database_file)
        finally:
            staged.unlink(missing_ok=True)

    def status_folder(self) -> Path:
        with self._uow_factory() as uow:
            return self._folder_from_value(uow.settings.get(self._FOLDER_KEY))

    def destination_identity(self) -> str:
        return os.path.normcase(str(self.status_folder().expanduser().resolve()))

    def has_daily_backup(self, destination: Path, work_date: date) -> bool:
        path = destination / f"{self._BACKUP_PREFIX}{work_date.isoformat()}.sqlite3"
        return path.is_file() and self._is_valid_database(path)

    def restore(self, backup: BackupView) -> Path:
        """Replace the live database from a verified backup and preserve a safety copy."""
        if not self._is_valid_database(backup.path):
            raise ValueError("The selected backup is no longer a valid SQLite database.")
        safety = self._create_safety_backup()
        staged = self._database.database_file.with_suffix(".restore.sqlite3")
        try:
            self._copy_database(backup.path, staged)
            if not self._is_valid_database(staged):
                raise ValueError("The selected backup could not be verified for restoration.")
            self._database.database_file.with_name(
                f"{self._database.database_file.name}-wal"
            ).unlink(missing_ok=True)
            self._database.database_file.with_name(
                f"{self._database.database_file.name}-shm"
            ).unlink(missing_ok=True)
            os.replace(staged, self._database.database_file)
        finally:
            staged.unlink(missing_ok=True)
        return safety

    def _create_daily_backup(
        self, today: str, folder: Path, *, cancelled: Callable[[], bool]
    ) -> BackupView:
        folder.mkdir(parents=True, exist_ok=True)
        destination = folder / f"{self._BACKUP_PREFIX}{today}.sqlite3"
        descriptor, name = tempfile.mkstemp(prefix=".qi-flow-backup-", suffix=".tmp", dir=folder)
        os.close(descriptor)
        staged = Path(name)
        try:
            self._copy_database(self._database.database_file, staged, cancelled=cancelled)
            if not self._is_valid_database(staged):
                raise sqlite3.DatabaseError("The completed backup failed integrity verification.")
            if cancelled():
                raise InterruptedError("Backup cancelled; the prior valid copy is unchanged.")
            os.replace(staged, destination)
        finally:
            staged.unlink(missing_ok=True)
        self._retain_newest(folder, keep=30)
        return BackupView(
            destination, datetime.fromtimestamp(destination.stat().st_mtime, COPENHAGEN)
        )

    def _create_safety_backup(self) -> Path:
        folder = self.status_folder()
        folder.mkdir(parents=True, exist_ok=True)
        timestamp = self._now().astimezone(COPENHAGEN).strftime("%Y-%m-%d-%H%M%S")
        destination = folder / f"qi-flow-safety-before-restore-{timestamp}.sqlite3"
        self._copy_database(self._database.database_file, destination)
        if not self._is_valid_database(destination):
            destination.unlink(missing_ok=True)
            raise sqlite3.DatabaseError("The current database could not be backed up safely.")
        return destination

    @staticmethod
    def _copy_database(
        source: Path,
        destination: Path,
        *,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> None:
        deadline = monotonic() + 30

        def progress(status: int, remaining: int, total: int) -> None:
            if cancelled():
                raise InterruptedError("Backup cancelled; the prior valid copy is unchanged.")
            if monotonic() >= deadline:
                raise TimeoutError("Database copy timed out; try again when storage is available.")

        progress(0, 0, 0)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with (
            closing(sqlite3.connect(source)) as source_connection,
            closing(sqlite3.connect(destination)) as target,
        ):
            source_connection.backup(target, pages=128, progress=progress, sleep=0.05)

    @staticmethod
    def _is_valid_database(path: Path) -> bool:
        deadline = monotonic() + 30
        try:
            with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as connection:
                connection.set_progress_handler(lambda: int(monotonic() >= deadline), 10000)
                result = connection.execute("PRAGMA integrity_check").fetchone()
                return result is not None and result[0] == "ok"
        except (OSError, sqlite3.Error):
            return False

    def _retain_newest(self, folder: Path, keep: int) -> None:
        for backup in self.list_backups(folder)[keep:]:
            backup.path.unlink(missing_ok=True)

    def _set_warning(self, warning: str) -> None:
        with self._uow_factory() as uow:
            uow.settings.save(self._WARNING_KEY, warning, self._now())

    def _clear_warning(self, today: str, folder: str) -> None:
        with self._uow_factory() as uow:
            uow.settings.save(self._LAST_SUCCESS_KEY, today, self._now())
            uow.settings.save(self._LAST_FOLDER_KEY, folder, self._now())
            uow.settings.save(self._WARNING_KEY, None, self._now())

    def _folder_from_value(self, value: object) -> Path:
        return (
            Path(value).expanduser().resolve()
            if isinstance(value, str) and value
            else self._default_folder
        )

    def _now(self) -> datetime:
        return self._clock.now()
