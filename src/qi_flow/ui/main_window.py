"""Main desktop shell; feature screens are implemented by later user stories."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent, QPixmap, QResizeEvent
from PySide6.QtWidgets import (
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from qi_flow import __version__
from qi_flow.application.dsb import DsbService
from qi_flow.application.google_sync import GoogleSyncSettings
from qi_flow.application.google_sync_service import SyncResult
from qi_flow.application.ports import GoogleConnection
from qi_flow.application.sync_actions import GoogleSyncActions
from qi_flow.application.testhuset import TesthusetCredentialStore, TesthusetService
from qi_flow.application.time_tracking import TimeTrackingApplicationService
from qi_flow.infrastructure.backups import BackupManager
from qi_flow.infrastructure.csv_export import CsvTimesheetExporter
from qi_flow.infrastructure.paths import AppPaths
from qi_flow.infrastructure.startup import StartupManager
from qi_flow.infrastructure.updates import ReleaseClient
from qi_flow.ui.google_sync_controller import GoogleSyncController
from qi_flow.ui.settings_page import SettingsPage
from qi_flow.ui.testhuset_dialog import SheetFactory
from qi_flow.ui.theme import ThemeManager, logo_path
from qi_flow.ui.timesheet_page import TimesheetPage
from qi_flow.ui.today_page import TodayPage


class MainWindow(QMainWindow):
    """Stable application shell for feature-owned pages."""

    close_app_requested = Signal()

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
        dsb: DsbService | None = None,
        dsb_sheet_factory: SheetFactory | None = None,
        google_sync: GoogleSyncSettings | None = None,
        google_oauth: GoogleConnection | None = None,
        releases: ReleaseClient | None = None,
        google_controller: GoogleSyncController | None = None,
        sync_command: Callable[[Callable[[], bool]], SyncResult] | None = None,
        sync_actions: GoogleSyncActions | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("QI Flow")
        self.resize(760, 860)
        app = QApplication.instance()
        assert isinstance(app, QApplication)
        self._theme_manager = ThemeManager(app)
        self._theme_manager.setParent(self)
        self._service = service
        self._tray_available = True
        self._exit_approved = False
        self._theme_manager.apply(service.app_preferences().theme if service else "system")

        self._navigation = QTabBar()
        self._navigation.setAccessibleName("Application pages")
        self._navigation.setExpanding(False)
        self._navigation.setDrawBase(False)
        self._navigation.setUsesScrollButtons(True)
        self._pages = QStackedWidget()
        self._page_index: dict[str, int] = {}

        pages: list[tuple[str, QWidget]] = []
        if service is not None:
            today_page = TodayPage(service, testhuset)
            today_page.open_timesheet_requested.connect(self.show_timesheet)
            pages.append(("Today", today_page))
        else:
            today_page = None
            pages.append(("Today", self._placeholder("Today", "Tracking service is unavailable.")))
        settings_page: SettingsPage | None = None
        if (
            service is not None
            and backups is not None
            and exporter is not None
            and paths is not None
            and startup is not None
        ):
            settings_page = SettingsPage(
                service,
                backups,
                exporter,
                paths,
                startup,
                testhuset,
                sheet_factory,
                credentials,
                dsb,
                dsb_sheet_factory,
                google_sync,
                google_oauth,
                releases,
                google_controller,
                sync_command,
                sync_actions,
            )
            if today_page is not None:
                settings_page.preferences_saved.connect(today_page.reload_configurable_options)
        timesheet_page = (
            TimesheetPage(service, testhuset, sheet_factory, dsb, dsb_sheet_factory)
            if service is not None
            else self._placeholder("Timesheet", "Tracking service is unavailable.")
        )
        if settings_page is not None and isinstance(timesheet_page, TimesheetPage):
            settings_page.dsb_enabled_changed.connect(timesheet_page.refresh_dsb_availability)
            settings_page.preferences_saved.connect(timesheet_page.refresh)
        self._settings_page = settings_page
        if settings_page is not None:
            settings_page.preferences_saved.connect(self._apply_saved_theme)
        pages.extend(
            (
                (
                    "Timesheet",
                    timesheet_page,
                ),
                (
                    "Settings",
                    settings_page
                    if settings_page is not None
                    else self._placeholder("Settings", "Settings are unavailable."),
                ),
            )
        )
        for title, page in pages:
            self._navigation.addTab(title)
            if title in ("Today", "Settings"):
                scroll = QScrollArea()
                scroll.setWidgetResizable(True)
                scroll.setWidget(page)
                self._page_index[title] = self._pages.addWidget(scroll)
            else:
                self._page_index[title] = self._pages.addWidget(page)

        self._navigation.currentChanged.connect(self._pages.setCurrentIndex)
        self._navigation.setCurrentIndex(0)

        navigation_panel = QWidget()
        navigation_panel.setObjectName("applicationHeader")
        navigation_layout = QGridLayout(navigation_panel)
        self._header_layout = navigation_layout
        navigation_layout.setContentsMargins(20, 12, 20, 12)
        navigation_layout.setSpacing(16)
        brand = QWidget()
        brand_layout = QHBoxLayout(brand)
        brand_layout.setContentsMargins(0, 0, 0, 0)
        brand_layout.setSpacing(16)
        self._brand = brand
        self._logo = QLabel()
        self._logo.setAccessibleName("TestHuset")
        self._theme_manager.changed.connect(self._update_logo)
        self._update_logo(self._theme_manager.resolved_theme)
        brand_layout.addWidget(self._logo)
        product = QLabel("QI Flow")
        product_font = product.font()
        product_font.setBold(True)
        product.setFont(product_font)
        brand_layout.addWidget(product)
        navigation_layout.addWidget(brand, 0, 0, Qt.AlignmentFlag.AlignLeft)
        navigation_layout.addWidget(self._navigation, 0, 1, Qt.AlignmentFlag.AlignRight)
        self._header_stacked = False
        self._version_label = QLabel(f"Version {__version__}")
        self._version_label.setObjectName("applicationVersion")
        self._version_label.setProperty("role", "muted")
        self._version_label.setContentsMargins(24, 4, 24, 8)
        self._close_app_button = QPushButton("Close app")
        self._close_app_button.clicked.connect(self.close_app_requested.emit)
        self._close_app_button.hide()
        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 20, 0)
        footer.addWidget(self._version_label, 1)
        footer.addWidget(self._close_app_button)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(navigation_panel)
        layout.addWidget(self._pages, 1)
        layout.addLayout(footer)
        self.setCentralWidget(content)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if not hasattr(self, "_header_layout"):
            return
        stacked = (
            self.width() < self._brand.sizeHint().width() + self._navigation.sizeHint().width() + 64
        )
        if stacked != self._header_stacked:
            self._header_layout.removeWidget(self._navigation)
            if stacked:
                self._header_layout.addWidget(
                    self._navigation, 1, 0, 1, 2, Qt.AlignmentFlag.AlignLeft
                )
            else:
                self._header_layout.addWidget(self._navigation, 0, 1, Qt.AlignmentFlag.AlignRight)
            self._header_stacked = stacked

    def _apply_saved_theme(self) -> None:
        if self._service is not None:
            self._theme_manager.apply(self._service.app_preferences().theme)

    def _update_logo(self, theme: str) -> None:
        self._logo.setPixmap(
            QPixmap(str(logo_path(theme))).scaled(
                160,
                32,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

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
        self._navigation.setCurrentIndex(self._page_index["Timesheet"])

    def show_settings(self) -> None:
        """Reveal the window on the Settings page, for the tray's Settings action."""
        self.reveal()
        self._navigation.setCurrentIndex(self._page_index["Settings"])

    def closeEvent(self, event: QCloseEvent) -> None:
        """Hide to tray, or request the ordinary exit confirmation when no tray exists."""
        if self._exit_approved:
            event.accept()
            return
        event.ignore()
        if self._tray_available:
            self.hide()
        else:
            self.close_app_requested.emit()

    def allow_exit(self) -> None:
        """Called only after the selected persistence action and worker shutdown succeed."""
        self._exit_approved = True

    def set_tray_available(self, available: bool) -> None:
        """Keep the explicit exit action accessible when Windows has no system tray."""
        self._tray_available = available
        self._close_app_button.setVisible(not available)
