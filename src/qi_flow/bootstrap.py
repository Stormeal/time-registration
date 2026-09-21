"""Composition root and Qt process lifecycle."""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import sys
from dataclasses import dataclass
from functools import partial
from importlib.resources import files
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QProcess
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from qi_flow import __version__
from qi_flow.application.dsb import DsbService
from qi_flow.application.google_sync import GoogleSyncSettings
from qi_flow.application.testhuset import TesthusetService
from qi_flow.application.time_tracking import TimeTrackingApplicationService
from qi_flow.infrastructure.backups import BackupManager
from qi_flow.infrastructure.csv_export import CsvTimesheetExporter
from qi_flow.infrastructure.dsb_browser import temporary_sheet as temporary_dsb_sheet
from qi_flow.infrastructure.google_oauth import GoogleOAuthStore
from qi_flow.infrastructure.logging import configure_logging
from qi_flow.infrastructure.paths import AppPaths
from qi_flow.infrastructure.single_instance import SingleInstanceGuard
from qi_flow.infrastructure.sqlite.database import SQLiteDatabase
from qi_flow.infrastructure.sqlite.repositories import SQLiteUnitOfWork
from qi_flow.infrastructure.startup import START_MINIMIZED_FLAG, create_startup_manager
from qi_flow.infrastructure.system import SystemClock, UuidIdentifierGenerator
from qi_flow.infrastructure.testhuset_browser import temporary_sheet
from qi_flow.infrastructure.testhuset_cache import JsonTaskCache
from qi_flow.infrastructure.testhuset_credentials import WindowsCredentialStore
from qi_flow.ui.exit_dialog import ExitCoordinator
from qi_flow.ui.main_window import MainWindow
from qi_flow.ui.tray import TrayController


@dataclass(frozen=True, slots=True)
class RuntimeContext:
    """Concrete infrastructure created at the composition root."""

    paths: AppPaths
    database: SQLiteDatabase


def _resolve_paths(data_root: Path | None) -> AppPaths:
    return AppPaths.for_root(data_root) if data_root is not None else AppPaths.from_qt()


def _guard_key(data_dir: Path) -> str:
    """A short, filesystem/pipe-name-safe key unique to one data directory (D036)."""
    return hashlib.sha1(str(data_dir).encode("utf-8")).hexdigest()[:16]


def build_runtime(data_root: Path | None = None) -> RuntimeContext:
    """Prepare local infrastructure without constructing widgets."""
    paths = _resolve_paths(data_root)
    paths.ensure()
    configure_logging(paths.log_dir)
    database = SQLiteDatabase(paths.database_file)
    database.initialize()
    return RuntimeContext(paths=paths, database=database)


def run(argv: list[str] | None = None) -> int:
    """Construct and run the desktop architecture shell."""
    QCoreApplication.setOrganizationName("QI Flow")
    QCoreApplication.setApplicationName("QI Flow")
    QCoreApplication.setApplicationVersion(__version__)

    args = list(argv if argv is not None else sys.argv)
    start_minimized = START_MINIMIZED_FLAG in args
    args = [value for value in args if value != START_MINIMIZED_FLAG]

    app = QApplication(args)
    app.setQuitOnLastWindowClosed(False)

    log = logging.getLogger(__name__)
    guard = SingleInstanceGuard(_guard_key(_resolve_paths(None).data_dir))
    if not guard.try_acquire():
        log.info("Another QI Flow instance is already running; it was asked to focus itself")
        return 0

    try:
        context = build_runtime()
    except sqlite3.DatabaseError as error:
        paths = _resolve_paths(None)
        available = BackupManager.valid_backups_in(paths.backup_dir)
        if not available:
            QMessageBox.critical(
                None,
                "QI Flow data could not be opened",
                "The local database is unreadable and no valid default-folder backup was found.\n\n"
                f"{error}",
            )
            guard.release()
            return 1
        newest = available[0]
        answer = QMessageBox.question(
            None,
            "Restore QI Flow data",
            "The local database is unreadable. Restore the newest verified backup from "
            f"{newest.created_at.strftime('%d/%m/%Y %H:%M')}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer is not QMessageBox.StandardButton.Yes:
            guard.release()
            return 1
        try:
            BackupManager.replace_unreadable_database(paths.database_file, newest)
        except (OSError, ValueError, sqlite3.Error) as restore_error:
            QMessageBox.critical(None, "Restore failed", str(restore_error))
            guard.release()
            return 1
        QProcess.startDetached(sys.executable, sys.argv[1:])
        guard.release()
        return 0
    log.info("QI Flow %s started; data directory initialized", __version__)

    service = TimeTrackingApplicationService(
        lambda: SQLiteUnitOfWork(context.database), SystemClock(), UuidIdentifierGenerator()
    )
    backups = BackupManager(
        context.database,
        context.paths.backup_dir,
        lambda: SQLiteUnitOfWork(context.database),
        SystemClock(),
    )
    backups.ensure_daily_backup()
    startup_manager = create_startup_manager()
    credentials = WindowsCredentialStore()
    dsb = DsbService(
        lambda: SQLiteUnitOfWork(context.database),
        SystemClock(),
        UuidIdentifierGenerator(),
        JsonTaskCache(context.paths.data_dir / "dsb-allocations.json"),
    )
    window = MainWindow(
        service,
        backups,
        CsvTimesheetExporter(lambda: SQLiteUnitOfWork(context.database)),
        context.paths,
        startup_manager,
        TesthusetService(
            lambda: SQLiteUnitOfWork(context.database),
            SystemClock(),
            UuidIdentifierGenerator(),
            JsonTaskCache(context.paths.data_dir / "testhuset-projects.json"),
        ),
        partial(temporary_sheet, credentials=credentials),
        credentials,
        dsb,
        temporary_dsb_sheet,
        GoogleSyncSettings(lambda: SQLiteUnitOfWork(context.database), SystemClock()),
        GoogleOAuthStore(),
    )
    preferences = service.app_preferences()
    if preferences.theme == "dark":
        app.setStyleSheet(
            "QWidget { background: #1f2929; color: #e9f5f3; } "
            "QPushButton { background: #087f78; padding: 6px; }"
        )
    elif preferences.theme == "light":
        app.setStyleSheet("QPushButton { background: #087f78; color: white; padding: 6px; }")
    guard.focus_requested.connect(window.reveal)

    icon = QIcon(str(files("qi_flow.assets").joinpath("qiflow-icon.svg")))
    app.setWindowIcon(icon)

    exit_coordinator = ExitCoordinator(service, window)
    exit_coordinator.exit_confirmed.connect(app.quit)

    if QSystemTrayIcon.isSystemTrayAvailable():
        tray = TrayController(icon, service, startup_manager)
        tray.open_requested.connect(window.reveal)
        tray.open_timesheet_requested.connect(window.show_timesheet)
        tray.settings_requested.connect(window.show_settings)
        tray.close_app_requested.connect(exit_coordinator.request_exit)
        tray.show()
    else:
        tray = None
        app.setQuitOnLastWindowClosed(True)
        log.warning("System tray unavailable; using window lifecycle")

    # Automatic startup stays in the tray unless an unfinished previous-day session needs
    # attention (D035); a manual launch, or one with no tray to fall back on, always shows it.
    first_setup = not service.setup_complete()
    show_window = (
        tray is None or not start_minimized or service.recovery_state() is not None or first_setup
    )
    if show_window:
        window.show()
    if first_setup:
        window.show_settings()
        QMessageBox.information(
            window,
            "Welcome to QI Flow",
            "Review the defaults in Settings, then choose Save application settings to finish "
            "setup.",
        )
    exit_code = app.exec()
    if tray is not None:
        tray.hide()
    guard.release()
    log.info("QI Flow stopped with exit code %d", exit_code)
    _ = context  # Keep runtime-owned adapters alive for the event-loop lifetime.
    return exit_code
