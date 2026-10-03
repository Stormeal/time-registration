"""Backup operations and views consumed by the desktop without adapter dependencies."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class BackupView:
    path: Path
    created_at: datetime


@dataclass(frozen=True, slots=True)
class BackupStatusView:
    folder: Path
    warning: str | None
    latest_backup: BackupView | None


class BackupOperations(Protocol):
    def status(self) -> BackupStatusView: ...
    def status_folder(self) -> Path: ...
    def destination_identity(self) -> str: ...
    def has_daily_backup(self, destination: Path, work_date: date) -> bool: ...
    def set_folder(self, folder: Path) -> BackupStatusView: ...
    def list_backups(self, folder: Path | None = None) -> list[BackupView]: ...
    def ensure_daily_backup(
        self,
        *,
        destination: Path | None = None,
        work_date: date | None = None,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> BackupView | None: ...
    def restore(self, backup: BackupView) -> Path: ...
