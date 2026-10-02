"""Correction editor for completed work sessions and their deductions."""

from __future__ import annotations

from datetime import date, datetime, time

from PySide6.QtCore import Qt, QTime
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTextEdit,
    QTimeEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from qi_flow.application.dto import (
    UpdateActiveWorkStartCommand,
    UpdateDayDetailsCommand,
    UpdateDeductionCommand,
    UpdateWorkSessionCommand,
)
from qi_flow.application.testhuset import TesthusetService
from qi_flow.application.time_tracking import TimeTrackingApplicationService
from qi_flow.domain.errors import DomainError
from qi_flow.domain.models import Deduction, DeductionKind, WorkLocation, WorkSession
from qi_flow.domain.time_rules import COPENHAGEN
from qi_flow.ui.controls import SettingsWheelGuard
from qi_flow.ui.daily_note_dialog import DailyNoteDialog
from qi_flow.ui.history_dialog import HistoryDialog
from qi_flow.ui.manual_entry_dialog import ManualEntryDialog


class SessionEditorDialog(QDialog):
    """Edit one selected day's records, including a running session's start time."""

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
        self.resize(1100, 600)
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(("Type", "Start", "Finish"))
        self._tree.setMinimumSize(220, 200)
        self._tree.setUniformRowHeights(True)
        self._tree.header().setStretchLastSection(False)
        self._tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2):
            self._tree.header().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        self._tree.itemSelectionChanged.connect(self._load_selected)
        self._start = QTimeEdit()
        self._end = QTimeEdit()
        for editor in (self._start, self._end):
            editor.setDisplayFormat("HH:mm")
            editor.setEnabled(False)
        self._save = QPushButton("Save correction")
        self._save.setProperty("role", "primary")
        self._save.setEnabled(False)
        self._delete = QPushButton("Delete selected")
        self._delete.setToolTip("Deleted entries can be restored from history for 30 days.")
        self._delete.setEnabled(False)
        self._add_lunch = QPushButton("Add lunch")
        self._add_lunch.setEnabled(False)
        self._add_lunch.clicked.connect(self._add_lunch_to_selected_session)
        self._history = QPushButton("View recoverable history")
        self._history.clicked.connect(self._open_history)
        self._save.clicked.connect(self._save_selected)
        self._delete.clicked.connect(self._delete_selected)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        form = QFormLayout()
        self._selection_help = QLabel()
        self._selection_help.setWordWrap(True)
        form.addRow(self._selection_help)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.addRow("Start", self._start)
        form.addRow("Finish", self._end)
        form.addRow(self._save, self._delete)
        self._office = QCheckBox("Worked from office")
        self._note = QTextEdit(self)
        self._note.hide()
        self._note.setPlaceholderText("Daily note")
        self._save_office = QPushButton("Save daily context")
        self._save_office.clicked.connect(self._save_office_status)
        self._edit_note = QPushButton("Daily note…")
        self._edit_note.clicked.connect(self._open_daily_note)
        context_form = QVBoxLayout()
        context_form.addWidget(self._office)
        context_form.addWidget(self._edit_note)
        context_form.addWidget(self._save_office)
        self._task = QComboBox()
        self._task_wheel_guard = SettingsWheelGuard(self)
        self._task.installEventFilter(self._task_wheel_guard)
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
        form.addRow(self._add_lunch)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        guidance = QLabel(
            "Select a session or lunch to correct its times. Changes save explicitly."
        )
        guidance.setWordWrap(True)
        layout.addWidget(guidance)
        left = QWidget()
        left.setMinimumWidth(240)
        left.setMaximumWidth(320)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self._tree, 1)
        left_layout.addWidget(self._history)
        controls = QWidget()
        controls_layout = QVBoxLayout(controls)
        correction = QGroupBox("Selected entry")
        correction.setLayout(form)
        controls_layout.addWidget(correction)
        context = QGroupBox(f"Daily context · {work_date:%d/%m/%Y}")
        context.setLayout(context_form)
        controls_layout.addWidget(context)
        if testhuset is not None:
            task_group = QGroupBox("Task assignment")
            task_layout = QVBoxLayout(task_group)
            task_layout.addWidget(self._task)
            task_layout.addWidget(self._save_task)
            controls_layout.addWidget(task_group)
        controls_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(controls)
        self._controls_scroll = scroll
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(scroll)
        splitter.setChildrenCollapsible(False)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([280, 780])
        layout.addWidget(splitter, 1)
        layout.addWidget(buttons)
        self._load_office_status()
        self._refresh()
        self._load_selected()

    def _refresh(self) -> None:
        selected = self._selected_value()
        selected_id = selected.id if selected is not None else None
        self._tree.clear()
        for session in self._service.completed_sessions_for_day(self._work_date):
            if session.actual_started_at.astimezone(COPENHAGEN).date() != self._work_date:
                continue
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
        active_session = self._service.active_session_for_day(self._work_date)
        if active_session is not None:
            session_item = self._item(
                "Work session (running)", active_session, active_session.actual_started_at, None
            )
            self._tree.addTopLevelItem(session_item)
            for deduction in self._service.completed_deductions(active_session.id):
                label = "Lunch" if deduction.kind.value == "lunch" else "Sleep break"
                session_item.addChild(
                    self._item(
                        label, deduction, deduction.actual_started_at, deduction.actual_ended_at
                    )
                )
        self._tree.expandAll()
        first = self._tree.topLevelItem(0)
        if first is not None:
            self._tree.setCurrentItem(first)
        for row in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(row)
            assert item is not None
            for candidate in [item, *(item.child(i) for i in range(item.childCount()))]:
                if candidate is None:
                    continue
                value = candidate.data(0, Qt.ItemDataRole.UserRole)
                if value.id == selected_id:
                    self._tree.setCurrentItem(candidate)

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
        is_running_session = isinstance(selected, WorkSession) and selected.is_active
        work_session_selected = isinstance(selected, WorkSession)
        self._save.setToolTip(
            "" if enabled else "Select a work session or lunch to correct its times."
        )
        self._delete.setToolTip(
            "Deleted entries can be restored from history for 30 days."
            if enabled
            else "Select a work session or lunch to delete."
        )
        self._add_lunch.setToolTip(
            "" if work_session_selected else "Select a work session to add a lunch interval."
        )
        self._start.setToolTip(
            "" if enabled else "Select a work session or lunch to edit its start time."
        )
        self._end.setToolTip(
            "Finish work from Today before changing a running session's end time."
            if is_running_session
            else ""
            if enabled
            else "Select a work session or lunch to edit its finish time."
        )
        task_help = (
            ""
            if work_session_selected
            else "Select a work session to edit its task assignment."
            if selected is None
            else "Task assignment applies to work sessions only."
        )
        self._task.setToolTip(task_help)
        self._save_task.setToolTip(task_help)
        self._selection_help.setText(
            "Running session: correct the start here. Finish work from Today."
            if is_running_session
            else "Adjust the selected interval, then save your correction."
            if enabled
            else "Choose a work session or lunch from the table."
        )
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
        self._end.setEnabled(enabled and not is_running_session)
        if selected is not None:
            self._start.setTime(self._as_qtime(selected.actual_started_at))
        if selected is not None and selected.actual_ended_at is not None:
            self._end.setTime(self._as_qtime(selected.actual_ended_at))
        self._loaded_interval = (
            self._start.time().toString("HH:mm"),
            self._end.time().toString("HH:mm"),
        )

    def _save_selected(self) -> None:
        selected = self._selected_value()
        if selected is None:
            return
        start = self._as_copenhagen(self._work_date, self._start.time())
        end = self._as_copenhagen(self._work_date, self._end.time())
        try:
            if isinstance(selected, WorkSession):
                if selected.is_active:
                    self._service.update_active_work_start(
                        UpdateActiveWorkStartCommand(selected.id, start)
                    )
                else:
                    self._service.update_work_session(
                        UpdateWorkSessionCommand(selected.id, start, end)
                    )
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
        answer = QMessageBox.question(
            self,
            "Delete time entry?",
            "Delete this entry from the timesheet? It can be restored from history for 30 days.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
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

    def _open_history(self) -> None:
        dialog = HistoryDialog(self._service, self._work_date)
        if dialog.exec():
            self._refresh()

    def _load_office_status(self) -> None:
        details = self._service.day_details(self._work_date)
        self._office.setChecked(details is not None and details.location is WorkLocation.OFFICE)
        self._note.setPlainText(details.note if details is not None else "")

    def _save_office_status(self) -> None:
        self._service.update_day_details(
            UpdateDayDetailsCommand(
                self._work_date,
                WorkLocation.OFFICE if self._office.isChecked() else WorkLocation.REMOTE,
                self._note.toPlainText(),
            )
        )

    def _open_daily_note(self) -> None:
        dialog = DailyNoteDialog(self._work_date, self._note.toPlainText(), self)
        if dialog.exec():
            self._note.setPlainText(dialog.note())
            self._save_office_status()

    def closeEvent(self, event: QCloseEvent) -> None:
        event.ignore()
        self.reject()

    def reject(self) -> None:
        details = self._service.day_details(self._work_date)
        saved_office = details is not None and details.location is WorkLocation.OFFICE
        saved_note = details.note if details is not None else ""
        selected = self._selected_value()
        dirty_interval = False
        if selected is not None:
            dirty_interval = (
                self._start.time().toString("HH:mm"),
                self._end.time().toString("HH:mm"),
            ) != self._loaded_interval
        if (
            self._office.isChecked() != saved_office
            or self._note.toPlainText() != saved_note
            or dirty_interval
        ):
            answer = QMessageBox.question(
                self,
                "Discard unsaved changes?",
                "Your correction has unsaved changes. Discard them?",
                QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Discard:
                return
        super().reject()

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

    @staticmethod
    def _as_copenhagen(work_date: date, value: QTime) -> datetime:
        """Use the minute shown in the editor; hidden QTime seconds are not an input."""
        return datetime.combine(work_date, time(value.hour(), value.minute()), tzinfo=COPENHAGEN)

    @staticmethod
    def _as_qtime(value: datetime) -> QTime:
        local = value.astimezone(COPENHAGEN)
        return QTime(local.hour, local.minute, local.second)
