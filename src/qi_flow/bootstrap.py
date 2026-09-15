"""Composition root and Qt process lifecycle."""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication, QStyle, QSystemTrayIcon

from qi_flow import __version__
from qi_flow.application.time_tracking import TimeTrackingApplicationService
from qi_flow.infrastructure.logging import configure_logging
from qi_flow.infrastructure.paths import AppPaths
from qi_flow.infrastructure.sqlite.database import SQLiteDatabase
from qi_flow.infrastructure.sqlite.repositories import SQLiteUnitOfWork
from qi_flow.infrastructure.system import SystemClock, UuidIdentifierGenerator
from qi_flow.ui.main_window import MainWindow
from qi_flow.ui.tray import TrayController


@dataclass(frozen=True, slots=True)
class RuntimeContext:
    """Concrete infrastructure created at the composition root."""

    paths: AppPaths
    database: SQLiteDatabase


def build_runtime(data_root: Path | None = None) -> RuntimeContext:
    """Prepare local infrastructure without constructing widgets."""
    paths = AppPaths.for_root(data_root) if data_root is not None else AppPaths.from_qt()
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

    app = QApplication(argv if argv is not None else sys.argv)
    app.setQuitOnLastWindowClosed(False)
    context = build_runtime()
    logging.getLogger(__name__).info("QI Flow %s started; data directory initialized", __version__)

    service = TimeTrackingApplicationService(
        lambda: SQLiteUnitOfWork(context.database), SystemClock(), UuidIdentifierGenerator()
    )
    window = MainWindow(service)
    icon = app.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
    app.setWindowIcon(icon)

    if QSystemTrayIcon.isSystemTrayAvailable():
        tray = TrayController(icon)
        tray.open_requested.connect(window.reveal)
        tray.close_app_requested.connect(app.quit)
        tray.show()
    else:
        tray = None
        app.setQuitOnLastWindowClosed(True)
        logging.getLogger(__name__).warning("System tray unavailable; using window lifecycle")

    window.show()
    exit_code = app.exec()
    if tray is not None:
        tray.hide()
    logging.getLogger(__name__).info("QI Flow stopped with exit code %d", exit_code)
    _ = context  # Keep runtime-owned adapters alive for the event-loop lifetime.
    return exit_code
