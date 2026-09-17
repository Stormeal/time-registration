"""Compact tray popover mirroring Today's state (US09, D033)."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCursor, QHideEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from qi_flow.application.dto import (
    ActiveStateView,
    FinishDeductionCommand,
    FinishWorkCommand,
    StartDeductionCommand,
    StartWorkCommand,
)
from qi_flow.application.time_tracking import TimeTrackingApplicationService
from qi_flow.domain.errors import DomainError
from qi_flow.domain.models import DeductionKind
from qi_flow.domain.time_rules import COPENHAGEN
from qi_flow.ui.formatting import format_duration
from qi_flow.ui.manual_entry_dialog import ManualEntryDialog


class TrayPanel(QWidget):
    """A frameless popup shown on left-click, kept in sync with the tray icon's own timer."""

    def __init__(
        self,
        service: TimeTrackingApplicationService,
        open_timesheet: Callable[[], None],
    ) -> None:
        super().__init__(None, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("trayPanel")
        self._service = service
        self._open_timesheet = open_timesheet

        frame = QFrame(self)
        frame.setObjectName("trayPanelFrame")
        frame.setFrameShape(QFrame.Shape.StyledPanel)

        self._status = QLabel()
        self._status.setWordWrap(True)
        self._timer = QLabel("00:00:00")
        timer_font = self._timer.font()
        timer_font.setPointSize(20)
        timer_font.setBold(True)
        self._timer.setFont(timer_font)
        self._detail = QLabel()
        self._detail.setWordWrap(True)

        self._start_work = QPushButton("Start work")
        self._lunch = QPushButton("Start lunch")
        self._finish_work = QPushButton("Finish work")
        self._add_entry = QPushButton("Add entry")
        self._open_timesheet_button = QPushButton("Open timesheet")

        actions = QHBoxLayout()
        actions.addWidget(self._start_work)
        actions.addWidget(self._lunch)
        actions.addWidget(self._finish_work)

        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.addWidget(self._status)
        layout.addWidget(self._timer)
        layout.addWidget(self._detail)
        layout.addLayout(actions)
        layout.addWidget(self._add_entry)
        layout.addWidget(self._open_timesheet_button)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(frame)

        self._start_work.clicked.connect(self._start)
        self._lunch.clicked.connect(self._toggle_lunch)
        self._finish_work.clicked.connect(self._finish)
        self._add_entry.clicked.connect(self._add_manual_entry)
        self._open_timesheet_button.clicked.connect(self._go_to_timesheet)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(1000)
        self._refresh_timer.timeout.connect(self.refresh)

    def show_near(self, anchor_rect: tuple[int, int, int, int] | None) -> None:
        """Position the panel near the tray icon (or the cursor, when its geometry is unknown)."""
        self.refresh()
        self._refresh_timer.start()
        self.adjustSize()
        size = self.sizeHint()
        if anchor_rect is not None and anchor_rect[2] > 0 and anchor_rect[3] > 0:
            x, y, width, _height = anchor_rect
            target_x = x + width // 2 - size.width() // 2
            target_y = max(0, y - size.height())
        else:
            cursor = QCursor.pos()
            target_x = cursor.x() - size.width() // 2
            target_y = max(0, cursor.y() - size.height())
        self.move(max(0, target_x), target_y)
        self.show()
        self.raise_()
        self.activateWindow()

    def hideEvent(self, event: QHideEvent) -> None:
        self._refresh_timer.stop()
        super().hideEvent(event)

    def refresh(self) -> None:
        state = self._service.active_state()
        blocked = (
            self._service.recovery_state() is not None
            or self._service.pending_sleep_gap() is not None
        )
        self._timer.setText(format_duration(state.net_seconds))
        if state.session_id is None:
            self._status.setText("Not tracking")
            self._detail.setText(
                "Open QI Flow to resolve an unfinished session." if blocked else ""
            )
            self._start_work.setEnabled(not blocked)
            self._lunch.setEnabled(False)
            self._lunch.setText("Start lunch")
            self._finish_work.setEnabled(False)
        elif state.active_deduction_kind is not None:
            self._status.setText("Lunch running")
            self._detail.setText(self._session_detail(state))
            self._start_work.setEnabled(False)
            self._lunch.setEnabled(not blocked)
            self._lunch.setText("End lunch")
            self._finish_work.setEnabled(False)
        else:
            self._status.setText("Working")
            self._detail.setText(self._session_detail(state))
            self._start_work.setEnabled(False)
            self._lunch.setEnabled(not blocked)
            self._lunch.setText("Start lunch")
            self._finish_work.setEnabled(not blocked)

    def _session_detail(self, state: ActiveStateView) -> str:
        if state.actual_started_at is None:
            return ""
        return f"Started {state.actual_started_at.astimezone(COPENHAGEN):%H:%M}"

    def _start(self) -> None:
        self._run(lambda: self._service.start_work(StartWorkCommand()))

    def _toggle_lunch(self) -> None:
        state = self._service.active_state()
        if state.active_deduction_kind is None:
            self._run(
                lambda: self._service.start_deduction(StartDeductionCommand(DeductionKind.LUNCH))
            )
        else:
            self._run(lambda: self._service.finish_deduction(FinishDeductionCommand()))

    def _finish(self) -> None:
        self._run(lambda: self._service.finish_work(FinishWorkCommand()))

    def _add_manual_entry(self) -> None:
        dialog = ManualEntryDialog(self._service)
        dialog.exec()
        self.refresh()

    def _go_to_timesheet(self) -> None:
        self.hide()
        self._open_timesheet()

    def _run(self, action: Callable[[], object]) -> None:
        try:
            action()
        except DomainError as error:
            QMessageBox.warning(self, "QI Flow", str(error))
        self.refresh()
