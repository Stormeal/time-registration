"""Today screen for the P0 timer flow."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from qi_flow.application.dto import (
    FinishDeductionCommand,
    FinishWorkCommand,
    StartDeductionCommand,
    StartWorkCommand,
)
from qi_flow.application.time_tracking import TimeTrackingApplicationService
from qi_flow.domain.errors import DomainError
from qi_flow.domain.models import DeductionKind
from qi_flow.domain.time_rules import COPENHAGEN


def _duration(seconds: int) -> str:
    hours, remainder = divmod(max(0, seconds), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}"


class TodayPage(QWidget):
    """Presents valid timer actions using an injected application service."""

    def __init__(self, service: TimeTrackingApplicationService) -> None:
        super().__init__()
        self._service = service
        self._heading = QLabel("Today")
        self._heading.setObjectName("todayHeading")
        font = self._heading.font()
        font.setPointSize(18)
        font.setBold(True)
        self._heading.setFont(font)
        self._status = QLabel()
        self._timer = QLabel("00:00:00")
        self._timer.setObjectName("netTimer")
        timer_font = self._timer.font()
        timer_font.setPointSize(28)
        timer_font.setBold(True)
        self._timer.setFont(timer_font)
        self._start_work = QPushButton("Start work")
        self._start_work.setObjectName("startWorkButton")
        self._lunch = QPushButton("Start lunch")
        self._lunch.setObjectName("lunchButton")
        self._finish_work = QPushButton("Finish work")
        self._finish_work.setObjectName("finishWorkButton")
        self._undo = QPushButton("Undo last timer action")
        self._undo.setObjectName("undoTimerButton")
        self._rounding = QComboBox()
        self._rounding.setObjectName("roundingCombo")
        for minutes in (1, 5, 10, 15):
            self._rounding.addItem(f"{minutes} minutes", minutes)
        self._rounding.setCurrentIndex((1, 5, 10, 15).index(service.rounding_minutes))

        actions = QHBoxLayout()
        actions.addWidget(self._start_work)
        actions.addWidget(self._lunch)
        actions.addWidget(self._finish_work)
        actions.addWidget(self._undo)
        form = QFormLayout()
        form.addRow("Round completed intervals to", self._rounding)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.addWidget(self._heading)
        layout.addWidget(self._status)
        layout.addWidget(self._timer)
        layout.addLayout(actions)
        layout.addLayout(form)
        layout.addStretch(1)

        self._start_work.clicked.connect(self._start)
        self._lunch.clicked.connect(self._toggle_lunch)
        self._finish_work.clicked.connect(self._finish)
        self._undo.clicked.connect(self._undo_last_action)
        self._rounding.currentIndexChanged.connect(self._set_rounding)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(1000)
        self._refresh_timer.timeout.connect(self.refresh)
        self._refresh_timer.start()
        self.refresh()
        self._show_recovery_if_needed()

    def refresh(self) -> None:
        """Refresh display from persisted state, so restart and crash recovery match UI."""
        state = self._service.active_state()
        blocked = self._service.recovery_state() is not None
        self._timer.setText(_duration(state.net_seconds))
        self._undo.setEnabled(self._service.can_undo_timer_action())
        if state.session_id is None:
            self._status.setText("No work session is running.")
            self._start_work.setEnabled(not blocked)
            self._lunch.setEnabled(False)
            self._finish_work.setEnabled(False)
            self._lunch.setText("Start lunch")
        elif state.active_deduction_kind is not None:
            self._status.setText("Lunch is running. Net time is paused.")
            self._start_work.setEnabled(False)
            self._lunch.setEnabled(not blocked)
            self._lunch.setText("End lunch")
            self._finish_work.setEnabled(False)
        else:
            self._status.setText("Work is running.")
            self._start_work.setEnabled(False)
            self._lunch.setEnabled(not blocked)
            self._lunch.setText("Start lunch")
            self._finish_work.setEnabled(not blocked)

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

    def _undo_last_action(self) -> None:
        self._run(self._service.undo_last_timer_action)

    def _set_rounding(self) -> None:
        self._service.set_rounding_minutes(int(self._rounding.currentData()))

    def _run(self, action: Callable[[], object]) -> None:
        try:
            action()
        except DomainError as error:
            QMessageBox.warning(self, "QI Flow", str(error))
        self.refresh()

    def _show_recovery_if_needed(self) -> None:
        recovery = self._service.recovery_state()
        if recovery is None:
            return
        message = QMessageBox(self)
        message.setWindowTitle("Resolve unfinished work")
        message.setText(
            "A work session from a previous day is still running. Choose how to resolve it."
        )
        finish = message.addButton("Set finish time", QMessageBox.ButtonRole.ActionRole)
        delete = message.addButton("Delete session", QMessageBox.ButtonRole.DestructiveRole)
        continue_button = message.addButton("Continue session", QMessageBox.ButtonRole.AcceptRole)
        review = message.addButton("Open timesheet", QMessageBox.ButtonRole.RejectRole)
        message.exec()
        chosen = message.clickedButton()
        if chosen is continue_button:
            self._service.continue_recovery()
        elif chosen is delete:
            self._service.delete_recovery()
        elif chosen is finish:
            value, accepted = QInputDialog.getText(
                self, "Set actual finish", "Finish (dd/MM/yyyy HH:mm)", text=""
            )
            if accepted:
                try:
                    local_time = datetime.strptime(value, "%d/%m/%Y %H:%M").replace(
                        tzinfo=COPENHAGEN
                    )
                    self._service.finish_work(FinishWorkCommand(local_time))
                except ValueError:
                    QMessageBox.warning(self, "QI Flow", "Use the format dd/MM/yyyy HH:mm.")
                except DomainError as error:
                    QMessageBox.warning(self, "QI Flow", str(error))
        elif chosen is review:
            self._status.setText("Open Timesheet to review the unresolved session.")
        self.refresh()
