"""Correction editor for completed work sessions and their deductions."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import cast

from PySide6.QtCore import Qt, QTime
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QMessageBox,
    QPushButton,
    QTimeEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from qi_flow.application.dto import (
    UpdateDayDetailsCommand,
    UpdateDeductionCommand,
    UpdateWorkSessionCommand,
)
from qi_flow.application.testhuset import TesthusetService
from qi_flow.application.time_tracking import TimeTrackingApplicationService
from qi_flow.domain.errors import DomainError
from qi_flow.domain.models import Deduction, DeductionKind, WorkLocation, WorkSession
from qi_flow.domain.time_rules import COPENHAGEN
from qi_flow.ui.manual_entry_dialog import ManualEntryDialog


class SessionEditorDialog(QDialog):
    """Edit completed exact-minute records for one selected timesheet date."""

    def __init__(
        self,
        service: TimeTrackingApplicationService,
        work_date: date,
        testhuset: TesthusetService | None = None,
    ) -> None:
        super().__init__()
        self._service = service
        self._work_date = work_date
        self._testhuset = testhuset
        self.setWindowTitle(f"Edit sessions - {work_date:%d/%m/%Y}")
        self.resize(620, 420)
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(("Type", "Start", "Finish"))
        self._tree.itemSelectionChanged.connect(self._load_selected)
        self._start = QTimeEdit()
        self._end = QTimeEdit()
        for editor in (self._start, self._end):
            editor.setDisplayFormat("HH:mm")
            editor.setEnabled(False)
        self._save = QPushButton("Save correction")
        self._save.setEnabled(False)
        self._delete = QPushButton("Delete selected")
        self._delete.setEnabled(False)
        self._add_lunch = QPushButton("Add lunch")
        self._add_lunch.setEnabled(False)
        self._add_lunch.clicked.connect(self._add_lunch_to_selected_session)
        self._save.clicked.connect(self._save_selected)
        self._delete.clicked.connect(self._delete_selected)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        form = QFormLayout()
        form.addRow("Start", self._start)
        form.addRow("Finish", self._end)
        form.addRow(self._save, self._delete)
        self._office = QCheckBox("Worked from office")
        self._save_office = QPushButton("Save office status")
        self._save_office.clicked.connect(self._save_office_status)
        form.addRow(self._office)
        form.addRow(self._save_office)
        self._task = QComboBox()
        self._task.addItem("Use configured default", None)
        if testhuset is not None:
            try:
                for task in testhuset.tasks():
                    self._task.addItem(task.label, task.id)
            except (ValueError, OSError):
                self._task.addItem("Scan Testhuset tasks in Settings", None)
        self._save_task = QPushButton("Save task assignment")
        self._save_task.clicked.connect(self._assign_task)
        self._task.setEnabled(False)
        self._save_task.setEnabled(False)
        if testhuset is not None:
            form.addRow("Testhuset task", self._task)
            form.addRow(self._save_task)
        layout = QVBoxLayout(self)
        layout.addWidget(self._tree)
        layout.addLayout(form)
        layout.addWidget(self._add_lunch)
        layout.addWidget(buttons)
        self._load_office_status()
        self._refresh()

    def _refresh(self) -> None:
        self._tree.clear()
        for session in self._service.completed_sessions_for_day(self._work_date):
            session_item = self._item(
                "Work session", session, session.actual_started_at, session.actual_ended_at
            )
            self._tree.addTopLevelItem(session_item)
            for deduction in self._service.completed_deductions(session.id):
                label = "Lunch" if deduction.kind.value == "lunch" else "Sleep break"
                session_item.addChild(
                    self._item(
                        label, deduction, deduction.actual_started_at, deduction.actual_ended_at
                    )
                )
        self._tree.expandAll()

    def _item(
        self, label: str, value: WorkSession | Deduction, started: datetime, ended: datetime | None
    ) -> QTreeWidgetItem:
        item = QTreeWidgetItem([label, self._time(started), self._time(ended)])
        item.setData(0, Qt.ItemDataRole.UserRole, value)
        return item

    def _load_selected(self) -> None:
        selected = self._selected_value()
        enabled = selected is not None
        self._task.setEnabled(isinstance(selected, WorkSession))
        self._save_task.setEnabled(isinstance(selected, WorkSession))
        self._add_lunch.setEnabled(isinstance(selected, WorkSession))
        if isinstance(selected, WorkSession):
            index = self._task.findData(selected.testhuset_task_id)
            if index < 0:
                self._task.addItem(
                    "Unavailable task — choose a current task", selected.testhuset_task_id
                )
                index = self._task.count() - 1
            self._task.setCurrentIndex(index)
        self._save.setEnabled(enabled)
        self._delete.setEnabled(enabled)
        self._start.setEnabled(enabled)
        self._end.setEnabled(enabled)
        if selected is not None and selected.actual_ended_at is not None:
            self._start.setTime(self._as_qtime(selected.actual_started_at))
            self._end.setTime(self._as_qtime(selected.actual_ended_at))

    def _save_selected(self) -> None:
        selected = self._selected_value()
        if selected is None:
            return
        start = self._as_copenhagen(self._start.time())
        end = self._as_copenhagen(self._end.time())
        try:
            if isinstance(selected, WorkSession):
                self._service.update_work_session(UpdateWorkSessionCommand(selected.id, start, end))
            else:
                self._service.update_deduction(UpdateDeductionCommand(selected.id, start, end))
        except DomainError as error:
            QMessageBox.warning(self, "QI Flow", str(error))
            return
        self._refresh()

    def _delete_selected(self) -> None:
        selected = self._selected_value()
        if selected is None:
            return
        try:
            if isinstance(selected, WorkSession):
                self._service.delete_work_session(selected.id)
            else:
                self._service.delete_deduction(selected.id)
        except DomainError as error:
            QMessageBox.warning(self, "QI Flow", str(error))
            return
        self._refresh()

    def _add_lunch_to_selected_session(self) -> None:
        selected = self._selected_value()
        if not isinstance(selected, WorkSession):
            return
        dialog = ManualEntryDialog(
            self._service,
            self._work_date,
            deduction_kind=DeductionKind.LUNCH,
            parent_session_id=selected.id,
        )
        dialog.exec()
        self._refresh()

    def _load_office_status(self) -> None:
        details = self._service.day_details(self._work_date)
        self._office.setChecked(details is not None and details.location is WorkLocation.OFFICE)

    def _save_office_status(self) -> None:
        details = self._service.day_details(self._work_date)
        self._service.update_day_details(
            UpdateDayDetailsCommand(
                self._work_date,
                WorkLocation.OFFICE if self._office.isChecked() else WorkLocation.REMOTE,
                details.note if details is not None else "",
            )
        )

    def _assign_task(self) -> None:
        selected = self._selected_value()
        if isinstance(selected, WorkSession) and self._testhuset is not None:
            try:
                self._testhuset.assign(selected.id, self._task.currentData())
            except (ValueError, OSError) as error:
                QMessageBox.warning(self, "Testhuset task", str(error))
                return
            self._refresh()

    def _selected_value(self) -> WorkSession | Deduction | None:
        selected = self._tree.selectedItems()
        value = selected[0].data(0, Qt.ItemDataRole.UserRole) if selected else None
        return value if isinstance(value, (WorkSession, Deduction)) else None

    @staticmethod
    def _time(value: datetime | None) -> str:
        return value.astimezone(COPENHAGEN).strftime("%H:%M") if value else ""

    def _as_copenhagen(self, value: QTime) -> datetime:
        return datetime.combine(self._work_date, cast(time, value.toPython()), tzinfo=COPENHAGEN)

    @staticmethod
    def _as_qtime(value: datetime) -> QTime:
        local = value.astimezone(COPENHAGEN)
        return QTime(local.hour, local.minute, local.second)
