"""Explicit-save dialog for manual completed work and lunch entries."""

from __future__ import annotations

from datetime import datetime
from typing import cast

from PySide6.QtCore import QDateTime
from PySide6.QtWidgets import (
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QMessageBox,
)

from qi_flow.application.dto import ManualDeductionCommand, ManualWorkSessionCommand
from qi_flow.application.time_tracking import TimeTrackingApplicationService
from qi_flow.domain.errors import DomainError
from qi_flow.domain.models import DeductionKind, SessionId
from qi_flow.domain.time_rules import COPENHAGEN


class ManualEntryDialog(QDialog):
    """Collect a completed exact-minute interval; close confirms unsaved discard."""

    def __init__(self, service: TimeTrackingApplicationService) -> None:
        super().__init__()
        self._service = service
        self.setWindowTitle("Add time entry")
        self._kind = QComboBox()
        self._kind.addItem("Work session", "work")
        self._kind.addItem("Lunch", DeductionKind.LUNCH.value)
        self._kind.addItem("Sleep break", DeductionKind.SLEEP_BREAK.value)
        self._parent = QComboBox()
        self._start = QDateTimeEdit(QDateTime.currentDateTime())
        self._end = QDateTimeEdit(QDateTime.currentDateTime())
        for editor in (self._start, self._end):
            editor.setDisplayFormat("dd/MM/yyyy HH:mm")
            editor.setCalendarPopup(True)
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        layout = QFormLayout(self)
        layout.addRow("Type", self._kind)
        layout.addRow("Work session", self._parent)
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
                f"{session.actual_started_at.astimezone(COPENHAGEN):%d/%m %H:%M}"
                f" - {session.actual_ended_at.astimezone(COPENHAGEN):%H:%M}"
            )
            self._parent.addItem(label, str(session.id))

    def _update_parent_visibility(self) -> None:
        self._parent.setVisible(self._kind.currentData() != "work")

    def _save(self) -> None:
        start = self._as_copenhagen(self._start.dateTime())
        end = self._as_copenhagen(self._end.dateTime())
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
    def _as_copenhagen(value: QDateTime) -> datetime:
        return cast(datetime, value.toPython()).replace(tzinfo=COPENHAGEN)
