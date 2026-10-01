from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton
from pytestqt.qtbot import QtBot

from qi_flow import __version__
from qi_flow.application.time_tracking import TimeTrackingApplicationService
from qi_flow.infrastructure.backups import BackupManager
from qi_flow.infrastructure.csv_export import CsvTimesheetExporter
from qi_flow.infrastructure.paths import AppPaths
from qi_flow.infrastructure.sqlite.database import SQLiteDatabase
from qi_flow.infrastructure.sqlite.repositories import SQLiteUnitOfWork
from qi_flow.infrastructure.updates import AvailableUpdate, ReleaseClient
from qi_flow.ui.settings_page import SettingsPage, UpdateDownloadWorker


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


def newer_version() -> str:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", __version__)
    assert match is not None
    major, minor, patch = match.groups()
    return f"v{major}.{minor}.{int(patch) + 1}"


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
            "tag_name": f"v{__version__}",
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
    release_version = newer_version()
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
            "tag_name": release_version,
            "draft": False,
            "prerelease": False,
            "assets": [
                {
                    "name": "QI-Flow-Update.zip",
                    "browser_download_url": f"https://github.com/Stormeal/time-registration/releases/download/{release_version}/QI-Flow-Update.zip",
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
    qtbot.waitUntil(
        lambda: page._update_status.text() == f"Version {release_version} is available."
    )

    assert calls == 1
    assert not (paths.data_dir / "updates" / f"QI-Flow-{release_version}.zip").exists()


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
        "v0.2.7",
        "https://github.com/Stormeal/time-registration/releases/download/v0.2.7/QI-Flow-Update.zip",
        "a" * 64,
        104_857_600,
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        # QMessageBox.question returns the enum's integer value in PySide6.
        lambda *_args, **_kwargs: int(QMessageBox.StandardButton.Yes),
    )
    monkeypatch.setattr("qi_flow.ui.settings_page.UpdateDownloadWorker.start", lambda _worker: None)
    qtbot.addWidget(page)
    page.show()
    next(
        button
        for button in page.findChildren(QPushButton)
        if button.accessibleName() == "Open Updates and diagnostics"
    ).click()

    page._update_check_finished(update)
    assert page._update_worker is not None
    page._update_worker.progress.emit(52_428_800, 104_857_600)

    assert page._update_progress.isVisible()
    assert page._update_progress.value() == 50
    assert "50.0 MB of 100.0 MB" in page._update_status.text()


def test_real_yes_click_starts_update_download(tmp_path, qtbot: QtBot, monkeypatch) -> None:
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
    version = newer_version()
    update = AvailableUpdate(
        version,
        f"https://github.com/Stormeal/time-registration/releases/download/{version}/QI-Flow-Update.zip",
        "a" * 64,
        126_679_954,
    )
    monkeypatch.setattr(UpdateDownloadWorker, "start", lambda _worker: None)
    qtbot.addWidget(page)
    page.show()
    next(
        button
        for button in page.findChildren(QPushButton)
        if button.accessibleName() == "Open Updates and diagnostics"
    ).click()

    def accept_update() -> None:
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, QMessageBox)
        dialog.button(QMessageBox.StandardButton.Yes).click()

    QTimer.singleShot(0, accept_update)
    page._update_check_finished(update)

    assert isinstance(page._update_worker, UpdateDownloadWorker)
    assert page._update_progress.isVisible()
    assert not page._check_updates.isEnabled()


@pytest.mark.parametrize("dispatch_succeeds", [True, False])
def test_verified_download_starts_stable_launcher_before_quitting(
    tmp_path: Path, qtbot: QtBot, monkeypatch, dispatch_succeeds: bool
) -> None:
    from qi_flow.ui import settings_page

    database = SQLiteDatabase(tmp_path / "qi-flow.sqlite3")
    database.initialize()
    service = TimeTrackingApplicationService(
        lambda: SQLiteUnitOfWork(database), FixedClock(), FixedIds()
    )
    paths = AppPaths.for_root(tmp_path / "data")
    paths.ensure()
    page = SettingsPage(
        service,
        BackupManager(database, paths.backup_dir, lambda: SQLiteUnitOfWork(database), FixedClock()),
        CsvTimesheetExporter(lambda: SQLiteUnitOfWork(database)),
        paths,
        Startup(),
        releases=ReleaseClient(lambda *_args, **_kwargs: Response(b"{}")),
    )
    qtbot.addWidget(page)
    root = tmp_path / "Programs" / "QI Flow"
    current = root / "current"
    current.mkdir(parents=True)
    (current / "QI Flow.exe").write_bytes(b"app")
    launcher = root / "QI Flow Launcher.exe"
    launcher.write_bytes(b"launcher")
    archive = paths.data_dir / "updates" / "verified.zip"
    archive.parent.mkdir()
    archive.write_bytes(b"verified")
    page._pending_update = AvailableUpdate(
        newer_version(),
        "https://github.com/Stormeal/time-registration/releases/download/next/QI-Flow-Update-v2.zip",
        "a" * 64,
        len(b"verified"),
    )
    started: list[tuple[str, list[str], str]] = []
    quit_called: list[bool] = []
    monkeypatch.setattr(settings_page.sys, "executable", str(current / "QI Flow.exe"))
    monkeypatch.setattr(
        settings_page.QProcess,
        "startDetached",
        lambda exe, args, cwd: (started.append((exe, args, cwd)) or dispatch_succeeds, 123),
    )
    monkeypatch.setattr(settings_page.QCoreApplication, "quit", lambda: quit_called.append(True))
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args, **_kwargs: None)

    page._update_downloaded(archive)

    assert len(started) == 1
    executable, arguments, cwd = started[0]
    assert executable == str(launcher)
    assert arguments == [
        "--apply-update",
        "--pid",
        str(settings_page.QCoreApplication.applicationPid()),
        "--archive",
        str(archive),
        "--sha256",
        "a" * 64,
    ]
    assert cwd == str(root)
    assert quit_called == ([True] if dispatch_succeeds else [])
