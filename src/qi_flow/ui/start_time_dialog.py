"""Start forgotten work or correct the active start using explicit persistence."""

from datetime import datetime

from PySide6.QtCore import QDate, QDateTime, Qt, QTime
from PySide6.QtWidgets import QDateTimeEdit, QDialog, QDialogButtonBox, QLabel, QVBoxLayout, QWidget

from qi_flow.application.dto import StartWorkAtCommand, UpdateActiveWorkStartCommand
from qi_flow.application.time_tracking import TimeTrackingApplicationService
from qi_flow.domain.errors import DomainError
from qi_flow.domain.time_rules import COPENHAGEN


class StartTimeDialog(QDialog):
    def __init__(
        self, service: TimeTrackingApplicationService, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._service = service
        state = service.active_state()
        self._session_id = state.session_id
        self.setWindowTitle("Change work start" if self._session_id else "Start work at a time")
        self.resize(420, 220)
        initial = (state.actual_started_at or service.current_time()).astimezone(COPENHAGEN)
        self._start = QDateTimeEdit(
            QDateTime(
                QDate(initial.year, initial.month, initial.day), QTime(initial.hour, initial.minute)
            )
        )
        self._start.setDisplayFormat("dd/MM/yyyy HH:mm")
        self._start.setCalendarPopup(True)
        self._error = QLabel()
        self._error.setWordWrap(True)
        self._error.setTextFormat(Qt.TextFormat.PlainText)
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.accepted.connect(self._save)
        self._buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        help_text = QLabel(
            "Enter when you actually started work (Copenhagen time). "
            "The timer continues from that time."
        )
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        layout.addWidget(self._start)
        layout.addWidget(self._error)
        layout.addWidget(self._buttons)

    def _save(self) -> None:
        value = self._start.dateTime()
        day, time = value.date(), value.time()
        start = datetime(
            day.year(), day.month(), day.day(), time.hour(), time.minute(), tzinfo=COPENHAGEN
        )
        try:
            if self._session_id is None:
                self._service.start_work_at(StartWorkAtCommand(start))
            else:
                self._service.update_active_work_start(
                    UpdateActiveWorkStartCommand(self._session_id, start)
                )
        except (DomainError, ValueError) as error:
            self._error.setText(str(error))
            return
        self.accept()
