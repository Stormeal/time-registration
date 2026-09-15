"""Main desktop shell; feature screens are implemented by later user stories."""

from __future__ import annotations

from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from qi_flow.application.time_tracking import TimeTrackingApplicationService
from qi_flow.ui.today_page import TodayPage


class MainWindow(QMainWindow):
    """Stable application shell for feature-owned pages."""

    def __init__(self, service: TimeTrackingApplicationService | None = None) -> None:
        super().__init__()
        self.setWindowTitle("QI Flow")
        self.resize(920, 620)

        self._navigation = QListWidget()
        self._navigation.setFixedWidth(170)
        self._pages = QStackedWidget()

        pages: list[tuple[str, QWidget]] = []
        if service is not None:
            pages.append(("Today", TodayPage(service)))
        else:
            pages.append(("Today", self._placeholder("Today", "Tracking service is unavailable.")))
        pages.extend(
            (
                (
                    "Timesheet",
                    self._placeholder(
                        "Timesheet", "Month and week views will be implemented by US12-US13."
                    ),
                ),
                (
                    "Settings",
                    self._placeholder(
                        "Settings", "Setup and local resilience will be implemented by US14-US20."
                    ),
                ),
            )
        )
        for title, page in pages:
            item = QListWidgetItem(title)
            self._navigation.addItem(item)
            self._pages.addWidget(page)

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

    def closeEvent(self, event: QCloseEvent) -> None:
        """Hide to tray; explicit process exit is owned by the tray controller."""
        event.ignore()
        self.hide()
