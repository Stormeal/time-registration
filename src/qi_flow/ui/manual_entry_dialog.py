"""Explicit-save dialog for manual completed work and lunch entries."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import cast

from PySide6.QtCore import QDate, QTime
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QMessageBox,
    QStackedWidget,
    QTimeEdit,
)

from qi_flow.application.dto import ManualDeductionCommand, ManualWorkSessionCommand
from qi_flow.application.time_tracking import TimeTrackingApplicationService
from qi_flow.domain.errors import DomainError
from qi_flow.domain.models import DeductionKind, SessionId
from qi_flow.domain.time_rules import COPENHAGEN


class ManualEntryDialog(QDialog):
    """Collect a completed exact-minute interval; close confirms unsaved discard."""

    def __init__(
        self, service: TimeTrackingApplicationService, work_date: date | None = None
    ) -> None:
        super().__init__()
        self._service = service
        self.setWindowTitle("Add time entry")
        self._kind = QComboBox()
        self._kind.addItem("Work session", "work")
        self._kind.addItem("Lunch", DeductionKind.LUNCH.value)
        self._kind.addItem("Sleep break", DeductionKind.SLEEP_BREAK.value)
        self._parent = QComboBox()
        initial = work_date or datetime.now(COPENHAGEN).date()
        current_time = datetime.now(COPENHAGEN).time().replace(second=0, microsecond=0)
        self._date = QDateEdit(QDate(initial.year, initial.month, initial.day))
        self._start = QTimeEdit(QTime(current_time.hour, current_time.minute))
        self._end = QTimeEdit(QTime(current_time.hour, current_time.minute))
        self._date.setDisplayFormat("dd/MM/yyyy")
        self._date.setCalendarPopup(True)
        for editor in (self._start, self._end):
            editor.setDisplayFormat("HH:mm")
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        layout = QFormLayout(self)
        layout.addRow("Type", self._kind)
        self._work_session = QStackedWidget()
        self._work_session.addWidget(self._date)
        self._work_session.addWidget(self._parent)
        layout.addRow("Work session", self._work_session)
        layout.addRow("Start", self._start)
        layout.addRow("End", self._end)
        layout.addRow(self._buttons)
        self._kind.currentIndexChanged.connect(self._update_parent_visibility)
        self._buttons.accepted.connect(self._save)
        self._buttons.rejected.connect(self.reject)
        self._load_sessions()
        self._update_parent_visibility()

    def _load_sessions(self) -> None:
        for session in self._service.completed_sessions():
            if session.actual_ended_at is None:
                continue
            label = (
                f"Work session: {session.actual_started_at.astimezone(COPENHAGEN):%d/%m/%Y} "
                f"{session.actual_started_at.astimezone(COPENHAGEN):%H:%M}"
                f" - {session.actual_ended_at.astimezone(COPENHAGEN):%H:%M}"
            )
            self._parent.addItem(label, str(session.id))

    def _update_parent_visibility(self) -> None:
        self._work_session.setCurrentWidget(
            self._date if self._kind.currentData() == "work" else self._parent
        )

    def _save(self) -> None:
        start = self._as_copenhagen(self._date.date(), self._start.time())
        end = self._as_copenhagen(self._date.date(), self._end.time())
        try:
            kind = self._kind.currentData()
            if kind == "work":
                self._service.add_manual_session(ManualWorkSessionCommand(start, end))
            else:
                session_id = self._parent.currentData()
                if session_id is None:
                    raise DomainError(
                        "Create a completed work session before adding a lunch or break."
                    )
                self._service.add_manual_deduction(
                    ManualDeductionCommand(SessionId(session_id), DeductionKind(kind), start, end)
                )
        except DomainError as error:
            QMessageBox.warning(self, "QI Flow", str(error))
            return
        self.accept()

    @staticmethod
    def _as_copenhagen(work_date: QDate, work_time: QTime) -> datetime:
        return datetime.combine(
            cast(date, work_date.toPython()), cast(time, work_time.toPython()), tzinfo=COPENHAGEN
        )
