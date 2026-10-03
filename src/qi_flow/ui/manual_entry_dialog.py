"""Explicit-save dialog for manual completed work and lunch entries."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime

from PySide6.QtCore import QDate, QTime
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFormLayout, QMessageBox

from qi_flow.application.dto import ManualDeductionCommand, ManualWorkSessionCommand
from qi_flow.application.time_tracking import TimeTrackingApplicationService
from qi_flow.domain.errors import DomainError
from qi_flow.domain.models import DeductionKind, SessionId, WorkSession
from qi_flow.domain.time_rules import COPENHAGEN
from qi_flow.ui.date_time_input import DateTimeInput


class ManualEntryDialog(QDialog):
    """Collect explicit endpoint dates and exact minutes without rollover inference."""

    def __init__(
        self,
        service: TimeTrackingApplicationService,
        work_date: date | None = None,
        deduction_kind: DeductionKind | None = None,
        parent_session_id: SessionId | None = None,
    ) -> None:
        super().__init__()
        self._service = service
        self._endpoints_edited = False
        self._suggesting = True
        self._sessions: dict[str, WorkSession] = {}
        self.setWindowTitle("Add time entry")
        self._kind = QComboBox()
        self._kind.addItem("Work session", "work")
        self._kind.addItem("Lunch", DeductionKind.LUNCH.value)
        self._kind.addItem("Break", DeductionKind.SLEEP_BREAK.value)
        self._parent = QComboBox()
        initial = work_date or service.today_summary().work_date
        current = datetime.now(COPENHAGEN)
        self._start_input = DateTimeInput(initial, "Start")
        self._end_input = DateTimeInput(initial, "End")
        self._start_date = self._date = self._start_input.date
        self._end_date = self._end_input.date
        self._start = self._start_input.time
        self._end = self._end_input.time
        for editor in (self._start, self._end):
            editor.setTime(QTime(current.hour, current.minute))
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        layout = QFormLayout(self)
        layout.addRow("Type", self._kind)
        layout.addRow("Work session", self._parent)
        layout.addRow("Start", self._start_input)
        layout.addRow("End", self._end_input)
        layout.addRow(self._buttons)
        self._kind.currentIndexChanged.connect(self._update_parent_visibility)
        self._parent.currentIndexChanged.connect(self._suggest_parent_dates)
        self._start_input.changed.connect(self._mark_endpoints_edited)
        self._end_input.changed.connect(self._mark_endpoints_edited)
        self._buttons.accepted.connect(self._save)
        self._buttons.rejected.connect(self.reject)
        self._load_sessions(initial)
        if deduction_kind is not None:
            self._kind.setCurrentIndex(self._kind.findData(deduction_kind.value))
            self._kind.setEnabled(False)
        if parent_session_id is not None:
            index = self._parent.findData(str(parent_session_id))
            if index >= 0:
                self._parent.setCurrentIndex(index)
            self._parent.setEnabled(False)
        self._update_parent_visibility()
        self._suggesting = False
        self._initial_values = self._form_values()

    def _load_sessions(self, work_date: date) -> None:
        sessions = self._service.completed_sessions()
        active = self._service.active_session_for_day(work_date)
        if active is not None:
            sessions.append(active)
        for session in sessions:
            self._sessions[str(session.id)] = session
            start = session.actual_started_at.astimezone(COPENHAGEN)
            end = session.actual_ended_at
            end_label = end.astimezone(COPENHAGEN).strftime("%d/%m/%Y %H:%M") if end else "running"
            self._parent.addItem(
                f"Work session: {start:%d/%m/%Y %H:%M} - {end_label}", str(session.id)
            )

    def _mark_endpoints_edited(self) -> None:
        if not self._suggesting:
            self._endpoints_edited = True

    def _update_parent_visibility(self) -> None:
        self._parent.setVisible(self._kind.currentData() != "work")
        self._suggest_parent_dates()

    def _suggest_parent_dates(self) -> None:
        if self._kind.currentData() == "work" or self._endpoints_edited:
            return
        session = self._sessions.get(self._parent.currentData())
        if session is None:
            return
        was_suggesting = self._suggesting
        self._suggesting = True
        start = session.actual_started_at.astimezone(COPENHAGEN).date()
        end = (session.actual_ended_at or session.actual_started_at).astimezone(COPENHAGEN).date()
        self._start_date.setDate(QDate(start.year, start.month, start.day))
        self._end_date.setDate(QDate(end.year, end.month, end.day))
        self._suggesting = was_suggesting

    def _save(self) -> bool:
        try:
            start = self._start_input.utc_value()
            end = self._end_input.utc_value()
            kind = self._kind.currentData()
            if kind == "work":
                self._service.add_manual_session(ManualWorkSessionCommand(start, end))
            else:
                session_id = self._parent.currentData()
                if session_id is None:
                    raise DomainError("Create a work session before adding a lunch or break.")
                self._service.add_manual_deduction(
                    ManualDeductionCommand(SessionId(session_id), DeductionKind(kind), start, end)
                )
        except (DomainError, OSError, sqlite3.Error) as error:
            QMessageBox.warning(self, "QI Flow", str(error))
            return False
        self.accept()
        return True

    def reject(self) -> None:
        if self._form_values() != self._initial_values:
            answer = QMessageBox.question(
                self,
                "Unsaved entry",
                "Save your time entry before closing?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer == QMessageBox.StandardButton.Save:
                self._save()
                return
            if answer != QMessageBox.StandardButton.Discard:
                return
        super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:
        event.ignore()
        self.reject()

    def _form_values(self) -> tuple[object, ...]:
        return (
            self._kind.currentData(),
            self._parent.currentData(),
            self._start_input.form_value(),
            self._end_input.form_value(),
        )
