"""Today screen for the P0 timer flow."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from qi_flow.application.dto import (
    FinishDeductionCommand,
    FinishWorkCommand,
    ReminderSettingsView,
    StartDeductionCommand,
    StartWorkCommand,
    UpdateDayDetailsCommand,
)
from qi_flow.application.time_tracking import TimeTrackingApplicationService
from qi_flow.domain.errors import DomainError
from qi_flow.domain.models import DeductionKind, WorkLocation
from qi_flow.domain.time_rules import COPENHAGEN
from qi_flow.ui.formatting import format_duration
from qi_flow.ui.manual_entry_dialog import ManualEntryDialog


class TodayPage(QWidget):
    """Presents valid timer actions using an injected application service."""

    def __init__(self, service: TimeTrackingApplicationService) -> None:
        super().__init__()
        self._service = service
        self._last_awake_at = datetime.now(UTC)
        self._sleep_deferred = False
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
        self._add_entry = QPushButton("Add entry")
        self._add_entry.setObjectName("addEntryButton")
        self._rounding = QComboBox()
        self._rounding.setObjectName("roundingCombo")
        for minutes in (1, 5, 10, 15):
            self._rounding.addItem(f"{minutes} minutes", minutes)
        self._rounding.setCurrentIndex((1, 5, 10, 15).index(service.rounding_minutes))
        self._rounding.setToolTip(
            "Rounds timer work starts down and finishes up to this boundary. "
            "Completed lunch boundaries use nearest rounding."
        )
        self._sleep_enabled = QCheckBox("Detect long Windows sleep")
        self._sleep_enabled.setToolTip(
            "Prompts you to resolve a Windows sleep gap. QI Flow never removes time automatically."
        )
        self._sleep_enabled.setChecked(service.sleep_detection_enabled())
        self._sleep_threshold = QSpinBox()
        self._sleep_threshold.setRange(1, 240)
        self._sleep_threshold.setSuffix(" minutes")
        self._sleep_threshold.setValue(service.sleep_threshold_seconds() // 60)
        self._sleep_threshold.setToolTip(
            "Prompts for a decision after a sleep gap longer than this."
        )
        reminders = service.reminder_settings()
        self._work_reminder_enabled = QCheckBox("Remind after long work")
        self._work_reminder_enabled.setToolTip(
            "Shows a reminder after this much elapsed work time, including lunch."
        )
        self._work_reminder_enabled.setChecked(reminders.work_enabled)
        self._work_reminder_minutes = QSpinBox()
        self._work_reminder_minutes.setRange(1, 24 * 60)
        self._work_reminder_minutes.setSuffix(" minutes")
        self._work_reminder_minutes.setValue(reminders.work_minutes)
        self._work_reminder_minutes.setToolTip("Sets the elapsed-work reminder threshold.")
        self._lunch_reminder_enabled = QCheckBox("Remind after long lunch")
        self._lunch_reminder_enabled.setToolTip(
            "Shows a reminder when the active lunch reaches this length."
        )
        self._lunch_reminder_enabled.setChecked(reminders.lunch_enabled)
        self._lunch_reminder_minutes = QSpinBox()
        self._lunch_reminder_minutes.setRange(1, 240)
        self._lunch_reminder_minutes.setSuffix(" minutes")
        self._lunch_reminder_minutes.setValue(reminders.lunch_minutes)
        self._lunch_reminder_minutes.setToolTip("Sets the active-lunch reminder threshold.")

        actions = QHBoxLayout()
        actions.addWidget(self._start_work)
        actions.addWidget(self._lunch)
        actions.addWidget(self._finish_work)
        actions.addWidget(self._undo)
        actions.addWidget(self._add_entry)
        form = QFormLayout()
        form.addRow("Round completed intervals to", self._rounding)
        form.addRow(self._sleep_enabled, self._sleep_threshold)
        form.addRow(self._work_reminder_enabled, self._work_reminder_minutes)
        form.addRow(self._lunch_reminder_enabled, self._lunch_reminder_minutes)
        self._office = QCheckBox("Worked from office")
        self._office.setToolTip("Marks today's daily context as office work for timesheet review.")
        self._note = QTextEdit()
        self._note.setPlaceholderText("Daily note")
        self._save_context = QPushButton("Save daily context")
        form.addRow(self._office)
        form.addRow("Note", self._note)
        form.addRow(self._save_context)
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
        self._add_entry.clicked.connect(self._add_manual_entry)
        self._save_context.clicked.connect(self._save_day_context)
        self._sleep_enabled.toggled.connect(self._save_sleep_settings)
        self._sleep_threshold.valueChanged.connect(self._save_sleep_settings)
        self._work_reminder_enabled.toggled.connect(self._save_reminder_settings)
        self._work_reminder_minutes.valueChanged.connect(self._save_reminder_settings)
        self._lunch_reminder_enabled.toggled.connect(self._save_reminder_settings)
        self._lunch_reminder_minutes.valueChanged.connect(self._save_reminder_settings)
        self._rounding.currentIndexChanged.connect(self._set_rounding)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(1000)
        self._refresh_timer.timeout.connect(self.refresh)
        self._refresh_timer.timeout.connect(self._check_sleep_gap)
        self._refresh_timer.start()
        self.refresh()
        self._load_day_context()
        self._show_recovery_if_needed()
        self._show_sleep_resolution_if_needed()

    def refresh(self) -> None:
        """Refresh display from persisted state, so restart and crash recovery match UI."""
        state = self._service.active_state()
        blocked = (
            self._service.recovery_state() is not None
            or self._service.pending_sleep_gap() is not None
        )
        self._timer.setText(format_duration(state.net_seconds))
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

    def reload_configurable_options(self) -> None:
        """Reflect values saved from Settings without waiting for an app restart."""
        preferences = self._service.app_preferences()
        controls = (self._rounding, self._sleep_enabled, self._sleep_threshold)
        for control in controls:
            control.blockSignals(True)
        try:
            self._rounding.setCurrentIndex((1, 5, 10, 15).index(preferences.rounding_minutes))
            self._sleep_enabled.setChecked(preferences.sleep_enabled)
            self._sleep_threshold.setValue(preferences.sleep_threshold_minutes)
        finally:
            for control in controls:
                control.blockSignals(False)

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

    def _add_manual_entry(self) -> None:
        dialog = ManualEntryDialog(self._service)
        dialog.exec()
        self.refresh()

    def _load_day_context(self) -> None:
        work_date = self._service.active_state().actual_started_at
        date_to_load = (
            work_date.astimezone(COPENHAGEN).date()
            if work_date
            else datetime.now(COPENHAGEN).date()
        )
        details = self._service.day_details(date_to_load)
        if details is not None:
            self._office.setChecked(details.location is WorkLocation.OFFICE)
            self._note.setPlainText(details.note)

    def _save_day_context(self) -> None:
        work_date = self._service.active_state().actual_started_at
        date_to_save = (
            work_date.astimezone(COPENHAGEN).date()
            if work_date
            else datetime.now(COPENHAGEN).date()
        )
        location = WorkLocation.OFFICE if self._office.isChecked() else WorkLocation.REMOTE
        self._service.update_day_details(
            UpdateDayDetailsCommand(date_to_save, location, self._note.toPlainText())
        )

    def _save_sleep_settings(self) -> None:
        self._service.set_sleep_detection(
            self._sleep_enabled.isChecked(), self._sleep_threshold.value()
        )

    def _save_reminder_settings(self) -> None:
        self._service.set_reminder_settings(
            ReminderSettingsView(
                work_enabled=self._work_reminder_enabled.isChecked(),
                work_minutes=self._work_reminder_minutes.value(),
                lunch_enabled=self._lunch_reminder_enabled.isChecked(),
                lunch_minutes=self._lunch_reminder_minutes.value(),
            )
        )

    def _check_sleep_gap(self) -> None:
        now = datetime.now(UTC)
        gap = self._service.detect_sleep_gap(self._last_awake_at, now)
        self._last_awake_at = now
        if gap is not None:
            self._sleep_deferred = False
            self._show_sleep_resolution_if_needed()

    def _show_sleep_resolution_if_needed(self) -> None:
        if self._sleep_deferred or self._service.pending_sleep_gap() is None:
            return
        message = QMessageBox(self)
        message.setWindowTitle("Resolve detected sleep")
        message.setText("QI Flow detected a long Windows sleep interval. How should it count?")
        include = message.addButton("Include as work", QMessageBox.ButtonRole.AcceptRole)
        exclude = message.addButton("Exclude as break", QMessageBox.ButtonRole.DestructiveRole)
        decide_later = message.addButton("Decide later", QMessageBox.ButtonRole.RejectRole)
        message.exec()
        if message.clickedButton() is include:
            self._service.resolve_sleep_gap("include")
        elif message.clickedButton() is exclude:
            self._service.resolve_sleep_gap("exclude")
        elif message.clickedButton() is decide_later:
            self._sleep_deferred = True
        self.refresh()

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
