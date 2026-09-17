"""Main desktop shell; feature screens are implemented by later user stories."""

from __future__ import annotations

from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from qi_flow.application.testhuset import TesthusetCredentialStore, TesthusetService
from qi_flow.application.time_tracking import TimeTrackingApplicationService
from qi_flow.infrastructure.backups import BackupManager
from qi_flow.infrastructure.csv_export import CsvTimesheetExporter
from qi_flow.infrastructure.paths import AppPaths
from qi_flow.infrastructure.startup import StartupManager
from qi_flow.ui.settings_page import SettingsPage
from qi_flow.ui.testhuset_dialog import SheetFactory
from qi_flow.ui.timesheet_page import TimesheetPage
from qi_flow.ui.today_page import TodayPage


class MainWindow(QMainWindow):
    """Stable application shell for feature-owned pages."""

    def __init__(
        self,
        service: TimeTrackingApplicationService | None = None,
        backups: BackupManager | None = None,
        exporter: CsvTimesheetExporter | None = None,
        paths: AppPaths | None = None,
        startup: StartupManager | None = None,
        testhuset: TesthusetService | None = None,
        sheet_factory: SheetFactory | None = None,
        credentials: TesthusetCredentialStore | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("QI Flow")
        self.resize(920, 620)

        self._navigation = QListWidget()
        self._navigation.setFixedWidth(170)
        self._pages = QStackedWidget()
        self._page_index: dict[str, int] = {}

        pages: list[tuple[str, QWidget]] = []
        if service is not None:
            pages.append(("Today", TodayPage(service)))
        else:
            pages.append(("Today", self._placeholder("Today", "Tracking service is unavailable.")))
        pages.extend(
            (
                (
                    "Timesheet",
                    TimesheetPage(service, testhuset, sheet_factory)
                    if service is not None
                    else self._placeholder("Timesheet", "Tracking service is unavailable."),
                ),
                (
                    "Settings",
                    SettingsPage(
                        service,
                        backups,
                        exporter,
                        paths,
                        startup,
                        testhuset,
                        sheet_factory,
                        credentials,
                    )
                    if service is not None
                    and backups is not None
                    and exporter is not None
                    and paths is not None
                    and startup is not None
                    else self._placeholder("Settings", "Settings are unavailable."),
                ),
            )
        )
        for title, page in pages:
            item = QListWidgetItem(title)
            self._navigation.addItem(item)
            if title == "Settings":
                scroll = QScrollArea()
                scroll.setWidgetResizable(True)
                scroll.setWidget(page)
                self._page_index[title] = self._pages.addWidget(scroll)
            else:
                self._page_index[title] = self._pages.addWidget(page)

        self._navigation.currentRowChanged.connect(self._pages.setCurrentIndex)
        self._navigation.setCurrentRow(0)

        content = QWidget()
        layout = QHBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._navigation)
        layout.addWidget(self._pages, 1)
        self.setCentralWidget(content)

    @staticmethod
    def _placeholder(title: str, description: str) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(32, 32, 32, 32)
        heading = QLabel(title)
        font = heading.font()
        font.setPointSize(18)
        font.setBold(True)
        heading.setFont(font)
        detail = QLabel(description)
        detail.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(detail)
        layout.addStretch(1)
        return page

    def reveal(self) -> None:
        """Show and focus the existing application window."""
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def show_timesheet(self) -> None:
        """Reveal the window on the Timesheet page, for the tray's Open timesheet action."""
        self.reveal()
        self._navigation.setCurrentRow(self._page_index["Timesheet"])

    def show_settings(self) -> None:
        """Reveal the window on the Settings page, for the tray's Settings action."""
        self.reveal()
        self._navigation.setCurrentRow(self._page_index["Settings"])

    def closeEvent(self, event: QCloseEvent) -> None:
        """Hide to tray; explicit process exit is owned by the tray controller."""
        event.ignore()
        self.hide()
