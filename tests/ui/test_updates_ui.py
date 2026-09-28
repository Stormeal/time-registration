from __future__ import annotations

import json
from datetime import UTC, datetime
from io import BytesIO

from PySide6.QtWidgets import QMessageBox
from pytestqt.qtbot import QtBot

from qi_flow.application.time_tracking import TimeTrackingApplicationService
from qi_flow.infrastructure.backups import BackupManager
from qi_flow.infrastructure.csv_export import CsvTimesheetExporter
from qi_flow.infrastructure.paths import AppPaths
from qi_flow.infrastructure.sqlite.database import SQLiteDatabase
from qi_flow.infrastructure.sqlite.repositories import SQLiteUnitOfWork
from qi_flow.infrastructure.updates import AvailableUpdate, ReleaseClient
from qi_flow.ui.settings_page import SettingsPage


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 9, 27, 10, tzinfo=UTC)


class FixedIds:
    def session_id(self) -> str:
        return "session-id"

    def deduction_id(self) -> str:
        return "deduction-id"

    def audit_id(self) -> str:
        return "audit-id"


class Startup:
    def is_enabled(self) -> bool:
        return False

    def set_enabled(self, _enabled: bool) -> None:
        pass


class Response(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def test_settings_can_check_release_feed_without_blocking_the_ui(tmp_path, qtbot: QtBot) -> None:
    database = SQLiteDatabase(tmp_path / "qi-flow.sqlite3")
    database.initialize()

    def unit_of_work() -> SQLiteUnitOfWork:
        return SQLiteUnitOfWork(database)

    clock = FixedClock()
    service = TimeTrackingApplicationService(unit_of_work, clock, FixedIds())
    paths = AppPaths.for_root(tmp_path / "data")
    paths.ensure()
    release = json.dumps(
        {
            "tag_name": "v0.2.4",
            "draft": False,
            "prerelease": False,
            "assets": [],
        }
    ).encode()
    page = SettingsPage(
        service,
        BackupManager(database, paths.backup_dir, unit_of_work, clock),
        CsvTimesheetExporter(unit_of_work),
        paths,
        Startup(),
        releases=ReleaseClient(lambda *_args, **_kwargs: Response(release)),
    )
    qtbot.addWidget(page)

    page._check_updates.click()
    qtbot.waitUntil(lambda: page._update_status.text() == "QI Flow is up to date.")

    assert page._check_updates.isEnabled()


def test_declining_an_update_does_not_download_the_package(
    tmp_path, qtbot: QtBot, monkeypatch
) -> None:
    database = SQLiteDatabase(tmp_path / "qi-flow.sqlite3")
    database.initialize()

    def unit_of_work() -> SQLiteUnitOfWork:
        return SQLiteUnitOfWork(database)

    clock = FixedClock()
    service = TimeTrackingApplicationService(unit_of_work, clock, FixedIds())
    paths = AppPaths.for_root(tmp_path / "data")
    paths.ensure()
    release = json.dumps(
        {
            "tag_name": "v0.2.5",
            "draft": False,
            "prerelease": False,
            "assets": [
                {
                    "name": "QI-Flow-Update.zip",
                    "browser_download_url": "https://github.com/Stormeal/time-registration/releases/download/v0.2.5/QI-Flow-Update.zip",
                    "digest": "sha256:" + "a" * 64,
                    "size": 5,
                }
            ],
        }
    ).encode()
    calls = 0

    def opener(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return Response(release)

    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.No,
    )
    page = SettingsPage(
        service,
        BackupManager(database, paths.backup_dir, unit_of_work, clock),
        CsvTimesheetExporter(unit_of_work),
        paths,
        Startup(),
        releases=ReleaseClient(opener),
    )
    qtbot.addWidget(page)

    page._check_updates.click()
    qtbot.waitUntil(lambda: page._update_status.text() == "Version v0.2.5 is available.")

    assert calls == 1
    assert not (paths.data_dir / "updates" / "QI-Flow-v0.2.5.zip").exists()


def test_update_download_progress_is_visible_and_reports_received_size(
    tmp_path, qtbot: QtBot, monkeypatch
) -> None:
    database = SQLiteDatabase(tmp_path / "qi-flow.sqlite3")
    database.initialize()

    def unit_of_work() -> SQLiteUnitOfWork:
        return SQLiteUnitOfWork(database)

    clock = FixedClock()
    service = TimeTrackingApplicationService(unit_of_work, clock, FixedIds())
    paths = AppPaths.for_root(tmp_path / "data")
    paths.ensure()
    page = SettingsPage(
        service,
        BackupManager(database, paths.backup_dir, unit_of_work, clock),
        CsvTimesheetExporter(unit_of_work),
        paths,
        Startup(),
        releases=ReleaseClient(lambda *_args, **_kwargs: Response(b"{}")),
    )
    update = AvailableUpdate(
        "v0.2.5",
        "https://github.com/Stormeal/time-registration/releases/download/v0.2.5/QI-Flow-Update.zip",
        "a" * 64,
        104_857_600,
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr("qi_flow.ui.settings_page.UpdateDownloadWorker.start", lambda _worker: None)
    qtbot.addWidget(page)
    page.show()

    page._update_check_finished(update)
    assert page._update_worker is not None
    page._update_worker.progress.emit(52_428_800, 104_857_600)

    assert page._update_progress.isVisible()
    assert page._update_progress.value() == 50
    assert "50.0 MB of 100.0 MB" in page._update_status.text()
