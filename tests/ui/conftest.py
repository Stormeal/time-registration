"""Isolated real adapters for compact UI interaction tests."""

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from qi_flow.application.time_tracking import TimeTrackingApplicationService
from qi_flow.infrastructure.backups import BackupManager
from qi_flow.infrastructure.csv_export import CsvTimesheetExporter
from qi_flow.infrastructure.paths import AppPaths
from qi_flow.infrastructure.sqlite.database import SQLiteDatabase
from qi_flow.infrastructure.sqlite.repositories import SQLiteUnitOfWork
from qi_flow.infrastructure.system import UuidIdentifierGenerator


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 9, 28, 10, 15, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value


class Startup:
    def __init__(self) -> None:
        self.enabled = False

    def is_enabled(self) -> bool:
        return self.enabled

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled


@pytest.fixture
def rig(tmp_path: Path):
    paths = AppPaths.for_root(tmp_path / "data")
    paths.ensure()
    database = SQLiteDatabase(paths.database_file)
    database.initialize()
    clock = MutableClock()
    startup = Startup()

    def uow():
        return SQLiteUnitOfWork(database)

    service = TimeTrackingApplicationService(uow, clock, UuidIdentifierGenerator())
    backups = BackupManager(database, paths.backup_dir, uow, clock)
    exporter = CsvTimesheetExporter(uow)
    return SimpleNamespace(
        service=service,
        clock=clock,
        database=database,
        paths=paths,
        startup=startup,
        backups=backups,
        exporter=exporter,
        uow=uow,
        window_args=(service, backups, exporter, paths, startup),
    )
