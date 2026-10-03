"""Composition root and Qt process lifecycle."""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from functools import partial
from importlib.resources import files
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QProcess
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from qi_flow import __version__
from qi_flow.application.backup_schedule import BackupSchedule
from qi_flow.application.dsb import DsbService
from qi_flow.application.google_sync import GoogleSyncConfiguration, GoogleSyncSettings
from qi_flow.application.google_sync_service import (
    SyncService,
)
from qi_flow.application.sync_actions import GoogleSyncActions
from qi_flow.application.sync_migration import SyncMigration
from qi_flow.application.sync_models import SyncTarget
from qi_flow.application.sync_schedule import SyncSchedule
from qi_flow.application.testhuset import TesthusetService
from qi_flow.application.time_tracking import TimeTrackingApplicationService
from qi_flow.infrastructure.backups import BackupManager
from qi_flow.infrastructure.csv_export import CsvTimesheetExporter
from qi_flow.infrastructure.dsb_browser import temporary_sheet as temporary_dsb_sheet
from qi_flow.infrastructure.google_oauth import GoogleOAuthStore
from qi_flow.infrastructure.google_sheets_sync import GoogleSheetsSync
from qi_flow.infrastructure.logging import configure_logging
from qi_flow.infrastructure.paths import AppPaths
from qi_flow.infrastructure.single_instance import SingleInstanceGuard
from qi_flow.infrastructure.sqlite.database import SQLiteDatabase
from qi_flow.infrastructure.sqlite.repositories import SQLiteUnitOfWork
from qi_flow.infrastructure.startup import START_MINIMIZED_FLAG, create_startup_manager
from qi_flow.infrastructure.sync_migration_backup import SQLiteMigrationSafety
from qi_flow.infrastructure.system import SystemClock, UuidIdentifierGenerator
from qi_flow.infrastructure.testhuset_browser import temporary_sheet
from qi_flow.infrastructure.testhuset_cache import JsonTaskCache
from qi_flow.infrastructure.testhuset_credentials import WindowsCredentialStore
from qi_flow.infrastructure.updates import ReleaseClient
from qi_flow.ui.backup_controller import BackupController
from qi_flow.ui.exit_dialog import ExitCoordinator
from qi_flow.ui.google_sync_controller import AutomaticSyncController, GoogleSyncController
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
    canonical = os.path.normcase(str(data_dir.resolve()))
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:16]


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
    try:
        data_dir = _resolve_paths(None).data_dir
        data_dir.mkdir(parents=True, exist_ok=True)
        guard = SingleInstanceGuard(
            _guard_key(data_dir), lock_path=data_dir / "qi-flow.instance.lock"
        )
        owns_database = guard.try_acquire()
    except OSError:
        QMessageBox.critical(
            None,
            "QI Flow could not start",
            "QI Flow could not establish exclusive access to its data directory. "
            "Check that the directory is accessible and writable, then try again.",
        )
        return 1
    if not owns_database:
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
        # Restoration has completed; the replacement must be able to own the data immediately.
        guard.release()
        launched, _pid = QProcess.startDetached(sys.executable, sys.argv[1:])
        if not launched:
            QMessageBox.critical(
                None,
                "QI Flow could not restart",
                "Your data was restored, but QI Flow could not restart automatically. "
                "Please open QI Flow again from the Start menu or your shortcut.",
            )
            return 1
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
    backup_controller = BackupController(
        backups, BackupSchedule(SystemClock(), backups.destination_identity), app
    )
    startup_manager = create_startup_manager()
    credentials = WindowsCredentialStore()
    dsb = DsbService(
        lambda: SQLiteUnitOfWork(context.database),
        SystemClock(),
        UuidIdentifierGenerator(),
        JsonTaskCache(context.paths.data_dir / "dsb-allocations.json"),
        testhuset_cache=JsonTaskCache(context.paths.data_dir / "testhuset-projects.json"),
    )
    google_controller = GoogleSyncController(app)
    google_settings = GoogleSyncSettings(lambda: SQLiteUnitOfWork(context.database), SystemClock())

    def configured_client_id() -> str | None:
        configuration = google_settings.load()
        return configuration.oauth_client_id if configuration is not None else None

    google_oauth = GoogleOAuthStore(client_id=configured_client_id)

    def sync_factory(
        configuration: GoogleSyncConfiguration,
        target: SyncTarget,
        generation: int,
        cancelled: Callable[[], bool],
    ) -> SyncService:
        return SyncService(
            lambda: SQLiteUnitOfWork(context.database),
            GoogleSheetsSync(configuration, google_oauth, target=target, cancelled=cancelled),
            target,
            SystemClock(),
            UuidIdentifierGenerator(),
            generation=generation,
        )

    def migration_factory(
        configuration: GoogleSyncConfiguration, generation: int, cancelled: Callable[[], bool]
    ) -> SyncMigration:
        clock = SystemClock()
        return SyncMigration(
            lambda: SQLiteUnitOfWork(context.database),
            GoogleSheetsSync(configuration, google_oauth, cancelled=cancelled),
            SQLiteMigrationSafety(context.database, context.paths.backup_dir),
            clock,
            UuidIdentifierGenerator(),
            generation=generation,
            cancelled=cancelled,
            deadline=clock.now() + timedelta(minutes=2),
        )

    sync_actions = GoogleSyncActions(
        lambda: SQLiteUnitOfWork(context.database),
        google_settings,
        google_oauth,
        SystemClock(),
        sync_factory,
        migration_factory,
    )
    automatic_sync = AutomaticSyncController(
        sync_actions, google_controller, SyncSchedule(SystemClock()), app
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
        google_settings,
        google_oauth,
        ReleaseClient(),
        google_controller,
        sync_actions=sync_actions,
        backup_controller=backup_controller,
    )
    guard.focus_requested.connect(window.reveal)

    icon = QIcon(str(files("qi_flow.assets").joinpath("qiflow-icon.svg")))
    app.setWindowIcon(icon)

    exit_coordinator = ExitCoordinator(service, window)
    shutting_down = False

    def finish_exit() -> None:
        if not shutting_down or google_controller.busy or backup_controller.busy:
            return
        window.allow_exit()
        app.quit()

    google_controller.ready_for_shutdown.connect(finish_exit)
    backup_controller.ready_for_shutdown.connect(finish_exit)

    def begin_exit() -> None:
        nonlocal shutting_down
        shutting_down = True
        automatic_sync.begin_shutdown()
        backup_controller.begin_shutdown()

    exit_coordinator.exit_confirmed.connect(begin_exit)
    window.close_app_requested.connect(exit_coordinator.request_exit)

    tray_available = QSystemTrayIcon.isSystemTrayAvailable()
    window.set_tray_available(tray_available)
    if tray_available:
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
    automatic_sync.begin_shutdown()
    google_controller.wait_for_shutdown()
    backup_controller.wait_for_shutdown()
    if tray is not None:
        tray.hide()
    guard.release()
    log.info("QI Flow stopped with exit code %d", exit_code)
    _ = context  # Keep runtime-owned adapters alive for the event-loop lifetime.
    return exit_code
