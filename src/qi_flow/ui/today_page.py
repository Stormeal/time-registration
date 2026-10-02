"""Today screen for compact tracking and daily context."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QResizeEvent, QShowEvent
from PySide6.QtWidgets import (
    QBoxLayout,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from qi_flow.application.dto import (
    FinishDeductionCommand,
    FinishWorkCommand,
    StartDeductionCommand,
    StartWorkCommand,
    UpdateDayDetailsCommand,
)
from qi_flow.application.testhuset import TesthusetService
from qi_flow.application.time_tracking import TimeTrackingApplicationService
from qi_flow.domain.errors import DomainError
from qi_flow.domain.models import DeductionKind, IsoWeek, WorkLocation
from qi_flow.domain.time_rules import COPENHAGEN
from qi_flow.ui.daily_note_dialog import DailyNoteDialog
from qi_flow.ui.formatting import format_duration
from qi_flow.ui.manual_entry_dialog import ManualEntryDialog
from qi_flow.ui.session_editor_dialog import SessionEditorDialog
from qi_flow.ui.start_time_dialog import StartTimeDialog


class TodayPage(QWidget):
    """Show service-owned state; keep edits intact across refresh and theme changes."""

    open_timesheet_requested = Signal()

    def __init__(
        self, service: TimeTrackingApplicationService, testhuset: TesthusetService | None = None
    ) -> None:
        super().__init__()
        self._service = service
        self._testhuset = testhuset
        self._last_awake_at = datetime.now(UTC)
        self._sleep_deferred = False
        self._context_date = service.today_summary().work_date
        self._context_initial = (False, "")
        self._context_deferred = False
        self._context_editing = False
        self._sessions_signature: object = None
        self._heading = self._label("Today", "heading")
        self._heading.setObjectName("todayHeading")
        self._date_label = self._label("", "muted")
        self._status = self._label("")
        self._timer = self._label("00:00:00")
        self._timer.setObjectName("netTimer")
        timer_font = self._timer.font()
        timer_font.setPointSize(28)
        timer_font.setBold(True)
        self._timer.setFont(timer_font)
        self._session_detail = self._label("", "muted")
        self._lunch_duration = self._label("", "muted")
        self._start_work = QPushButton("Start work")
        self._start_at = QPushButton("Start at…")
        self._start_at.clicked.connect(self._choose_start_time)
        self._start_work.setObjectName("startWorkButton")
        self._lunch = QPushButton("Start lunch")
        self._lunch.setObjectName("lunchButton")
        self._finish_work = QPushButton("Finish work")
        self._finish_work.setObjectName("finishWorkButton")
        self._undo = QPushButton("Undo last timer action")
        self._undo.setObjectName("undoTimerButton")
        self._add_entry = QPushButton("Add entry")
        self._add_entry.setObjectName("addEntryButton")
        self._resolve = QPushButton("Resolve unfinished time")
        self._resolve.setObjectName("resolveTimeButton")
        self._feedback = self._label("", "muted")
        self._day_total = self._label("00:00", "total")
        self._day_caption = self._label("", "muted")
        self._day_lunch = self._label("00:00", "total")
        self._week_total = self._label("", "total")
        self._week_caption = self._label("", "muted")
        self._week_relation = self._label("", "muted")
        self._sessions = self._label("", "muted")
        self._edit_sessions = QPushButton("Edit sessions")
        self._open_timesheet = QPushButton("Open timesheet")
        self._office = QCheckBox("Worked from office")
        self._office.setToolTip("Marks this date as office work for timesheet review.")
        self._note = QTextEdit(self)
        self._note.hide()
        self._note.setPlaceholderText("Daily note")
        self._note.setAccessibleName("Daily note")
        self._note.setMinimumHeight(76)
        self._note.setMaximumHeight(120)
        self._save_context = QPushButton("Save daily context")
        self._edit_note = QPushButton("Daily note…")
        self._edit_note.clicked.connect(self._open_daily_note)
        self._context_heading = self._label("")
        self._change_date = QPushButton("Review date change")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)
        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.addWidget(self._heading)
        titles.addWidget(self._date_label)
        head.addLayout(titles, 1)
        head.addWidget(self._add_entry)
        layout.addLayout(head)
        timer_row = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        timer_row.setSpacing(20)
        self._timer_row_layout = timer_row
        timer_info = QVBoxLayout()
        for widget in (self._status, self._timer, self._session_detail, self._lunch_duration):
            timer_info.addWidget(widget)
        timer_row.addLayout(timer_info, 1)
        buttons = QBoxLayout(QBoxLayout.Direction.TopToBottom)
        buttons.setSpacing(8)
        self._action_buttons_layout = buttons
        for action_button in (self._start_work, self._lunch, self._finish_work, self._start_at):
            buttons.addWidget(action_button)
        buttons.addStretch(1)
        timer_row.addLayout(buttons)
        layout.addLayout(timer_row)
        layout.addWidget(self._resolve)
        summary = QHBoxLayout()
        for labels in (
            (self._day_caption, self._day_total),
            (self._label("Lunch today · effective", "muted"), self._day_lunch),
            (self._week_caption, self._week_total, self._week_relation),
        ):
            column = QVBoxLayout()
            for widget in labels:
                column.addWidget(widget)
            column.addStretch(1)
            summary.addLayout(column, 1)
        layout.addWidget(self._separator())
        layout.addLayout(summary)
        notice = QHBoxLayout()
        notice.addWidget(self._feedback, 1)
        notice.addWidget(self._undo)
        layout.addLayout(notice)
        layout.addWidget(self._separator())
        session_header = QHBoxLayout()
        session_header.addWidget(self._label("Today's sessions"), 1)
        session_header.addWidget(self._edit_sessions)
        session_header.addWidget(self._open_timesheet)
        layout.addLayout(session_header)
        layout.addWidget(self._sessions)
        layout.addWidget(self._separator())
        layout.addWidget(self._context_heading)
        layout.addWidget(self._office)
        context_actions = QHBoxLayout()
        context_actions.addWidget(self._change_date)
        context_actions.addWidget(self._edit_note)
        context_actions.addStretch(1)
        context_actions.addWidget(self._save_context)
        layout.addLayout(context_actions)
        layout.addStretch(1)

        self._start_work.clicked.connect(self._start)
        self._lunch.clicked.connect(self._toggle_lunch)
        self._finish_work.clicked.connect(self._finish)
        self._undo.clicked.connect(self._undo_last_action)
        self._add_entry.clicked.connect(self._add_manual_entry)
        self._save_context.clicked.connect(self._save_day_context)
        self._resolve.clicked.connect(self._resolve_pending_time)
        self._edit_sessions.clicked.connect(self._open_session_editor)
        self._open_timesheet.clicked.connect(self.open_timesheet_requested.emit)
        self._change_date.clicked.connect(self._review_date_change)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(1000)
        self._refresh_timer.timeout.connect(self.refresh)
        self._refresh_timer.timeout.connect(self._check_sleep_gap)
        self._load_day_context()
        self.refresh()
        self._refresh_timer.start()
        if self._service.recovery_state() is not None:
            # The shell must connect navigation before a recovery choice can emit it.
            QTimer.singleShot(0, self, self._show_recovery_if_needed)
        self._show_sleep_resolution_if_needed()

    @staticmethod
    def _label(text: str, role: str = "") -> QLabel:
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        if role:
            label.setProperty("role", role)
        return label

    @staticmethod
    def _separator() -> QFrame:
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.HLine)
        return frame

    @staticmethod
    def _primary(button: QPushButton, active: bool) -> None:
        role = "primary" if active else ""
        if button.property("role") != role:
            button.setProperty("role", role)
            button.style().unpolish(button)
            button.style().polish(button)

    def refresh(self) -> None:
        """Update service-owned labels without recreating inputs or discarding edits."""
        state = self._service.active_state()
        recovery = self._service.recovery_state()
        gap = self._service.pending_sleep_gap()
        blocked = recovery is not None or gap is not None
        blocked_help = (
            "Resolve the unfinished previous-day session before using timer actions."
            if recovery is not None
            else "Resolve the detected sleep gap before using timer actions."
            if gap is not None
            else ""
        )
        running = state.session_id is not None
        on_lunch = state.active_deduction_kind is not None
        self._timer.setText(format_duration(state.net_seconds))
        self._start_work.setVisible(not running)
        self._start_work.setEnabled(not blocked and not running)
        self._lunch.setVisible(running)
        self._lunch.setEnabled(running and not blocked)
        self._lunch.setText("End lunch" if on_lunch else "Start lunch")
        self._finish_work.setVisible(running)
        self._finish_work.setEnabled(running and not on_lunch and not blocked)
        self._start_at.setText("Change start…" if running else "Start at…")
        self._start_at.setEnabled(not blocked)
        self._start_work.setToolTip(blocked_help if not running and blocked else "")
        self._lunch.setToolTip(blocked_help if running and blocked else "")
        self._start_at.setToolTip(blocked_help)
        self._primary(self._start_work, True)
        self._primary(self._lunch, True)
        self._status.setText("On lunch" if on_lunch else "Working" if running else "Not tracking")
        start = state.actual_started_at
        self._session_detail.setText(
            f"This session · started {start.astimezone(COPENHAGEN):%d/%m %H:%M} · actual net time"
            if start
            else "No active session"
        )
        self._lunch_duration.setVisible(on_lunch)
        if state.actual_deduction_started_at is not None:
            # Use the service's clock-backed live deduction view, not a second UI clock.
            elapsed = self._service.active_lunch_seconds()
            self._lunch_duration.setText(
                f"Lunch · {format_duration(elapsed)} · End lunch before finishing work."
            )
        self._finish_work.setToolTip(
            blocked_help
            if running and blocked
            else "End lunch before finishing work."
            if on_lunch
            else ""
        )
        self._resolve.setVisible(blocked)
        self._resolve.setText("Resolve previous-day work" if recovery else "Resolve detected sleep")
        self._undo.setVisible(self._service.can_undo_timer_action())
        self._undo.setEnabled(self._service.can_undo_timer_action())
        day = self._service.today_summary()
        self._date_label.setText(day.work_date.strftime("%A, %d/%m/%Y"))
        self._day_caption.setText(
            "Today total · effective" + (" · provisional" if day.is_provisional else "")
        )
        self._day_total.setText(format_duration(day.net_seconds)[:5])
        self._day_lunch.setText(format_duration(day.lunch_seconds)[:5])
        iso_year, iso_week, _ = day.work_date.isocalendar()
        week = self._service.weekly_progress(IsoWeek(iso_year, iso_week))
        self._week_caption.setText(f"Week {iso_week} · effective total")
        self._week_total.setText(
            f"{format_duration(week.logged_seconds)[:5]} / "
            f"{format_duration(week.target_minutes * 60)[:5]}"
        )
        relation = "remaining" if week.difference_seconds < 0 else "over target"
        self._week_relation.setText(
            f"{format_duration(abs(week.difference_seconds))[:5]} {relation}"
        )
        self._refresh_sessions(day.work_date)
        self._sync_context_date(day.work_date)

    def reload_configurable_options(self) -> None:
        self.refresh()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        dirty = self._context_initial != (self._office.isChecked(), self._note.toPlainText())
        if not dirty:
            self._load_day_context()
        self.refresh()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        wide = event.size().width() >= 720
        self._timer_row_layout.setDirection(
            QBoxLayout.Direction.LeftToRight if wide else QBoxLayout.Direction.TopToBottom
        )
        direction = QBoxLayout.Direction.TopToBottom
        if not wide and event.size().width() >= 520:
            direction = QBoxLayout.Direction.LeftToRight
        if self._action_buttons_layout.direction() != direction:
            self._action_buttons_layout.setDirection(direction)

    def _start(self) -> None:
        task_id = None
        if self._testhuset is not None:
            tasks = self._testhuset.tasks()
            if tasks:
                labels = [task.label for task in tasks]
                for index, label in enumerate(labels):
                    if labels.count(label) > 1:
                        labels[index] = f"{label} ({tasks[index].id})"
                default_id = self._testhuset.default_task_id()
                current = next(
                    (index for index, task in enumerate(tasks) if task.id == default_id), 0
                )
                selected, accepted = QInputDialog.getItem(
                    self,
                    "Choose EazyProject task",
                    "Project / task for this work session:",
                    labels,
                    current,
                    False,
                )
                if not accepted:
                    return
                try:
                    task_id = tasks[labels.index(selected)].id
                except ValueError:
                    return
        self._run(
            lambda: self._start_with_task(task_id),
            "Work started · Undo is available for 30 seconds.",
        )

    def _start_with_task(self, task_id: str | None) -> None:
        state = self._service.start_work(StartWorkCommand())
        if task_id is None or state.session_id is None or self._testhuset is None:
            return
        try:
            self._testhuset.assign_active(state.session_id, task_id)
        except Exception:
            self._service.undo_last_timer_action()
            raise

    def _toggle_lunch(self) -> None:
        if self._service.active_state().active_deduction_kind is None:
            self._run(
                lambda: self._service.start_deduction(StartDeductionCommand(DeductionKind.LUNCH)),
                "Lunch started · Undo is available for 30 seconds.",
            )
        else:
            self._run(
                lambda: self._service.finish_deduction(FinishDeductionCommand()),
                "Lunch ended · Undo is available for 30 seconds.",
            )

    def _finish(self) -> None:
        self._run(
            lambda: self._service.finish_work(FinishWorkCommand()),
            "Work finished · Undo is available for 30 seconds.",
        )

    def _undo_last_action(self) -> None:
        self._run(self._service.undo_last_timer_action, "Timer action undone.")

    def _add_manual_entry(self) -> None:
        ManualEntryDialog(self._service).exec()
        self.refresh()

    def _open_session_editor(self) -> None:
        dirty = self._context_initial != (self._office.isChecked(), self._note.toPlainText())
        SessionEditorDialog(
            self._service, self._service.today_summary().work_date, self._testhuset
        ).exec()
        if not dirty:
            self._load_day_context()
        self.refresh()

    def _refresh_sessions(self, work_date: date) -> None:
        sessions = self._service.completed_sessions_for_day(work_date)
        state = self._service.active_state()
        started_at = state.actual_started_at
        active = (
            self._service.active_session_for_day(started_at.astimezone(COPENHAGEN).date())
            if started_at is not None and started_at.astimezone(COPENHAGEN).date() <= work_date
            else None
        )
        if active is not None:
            sessions = [*sessions, active]
        signature = (work_date, tuple((s.id, s.revision, s.actual_ended_at) for s in sessions))
        if signature == self._sessions_signature:
            return
        self._sessions_signature = signature
        lines = []
        for session in sessions:
            start = session.effective_started_at or session.actual_started_at
            end = session.effective_ended_at or session.actual_ended_at
            local_start = start.astimezone(COPENHAGEN)
            start_label = local_start.strftime(
                "%H:%M" if local_start.date() == work_date else "%d/%m %H:%M"
            )
            lines.append(
                f"{start_label} - {end.astimezone(COPENHAGEN):%H:%M}"
                if end
                else f"{start_label} - in progress"
            )
        self._sessions.setText("\n".join(lines) if lines else "No sessions recorded today.")

    def _resolve_pending_time(self) -> None:
        self._sleep_deferred = False
        self._show_recovery_if_needed()
        self._show_sleep_resolution_if_needed()
        self.refresh()

    def _load_day_context(self) -> None:
        details = self._service.day_details(self._context_date)
        self._office.setChecked(details is not None and details.location is WorkLocation.OFFICE)
        self._note.setPlainText(details.note if details else "")
        self._context_initial = (self._office.isChecked(), self._note.toPlainText())
        self._context_heading.setText(f"Daily context · {self._context_date:%d/%m/%Y}")

    def _open_daily_note(self) -> None:
        dialog = DailyNoteDialog(self._context_date, self._note.toPlainText(), self)
        self._context_editing = True
        try:
            accepted = dialog.exec()
        finally:
            self._context_editing = False
        if accepted:
            self._note.setPlainText(dialog.note())
            self._save_day_context()
        else:
            self.refresh()

    def _choose_start_time(self) -> None:
        dialog = StartTimeDialog(self._service, self)
        if dialog.exec():
            self._feedback.setText("Actual start time saved.")
        self.refresh()

    def _save_day_context(self) -> None:
        location = WorkLocation.OFFICE if self._office.isChecked() else WorkLocation.REMOTE
        try:
            self._service.update_day_details(
                UpdateDayDetailsCommand(self._context_date, location, self._note.toPlainText())
            )
        except (DomainError, ValueError) as error:
            QMessageBox.warning(self, "Could not save daily context", str(error))
            return
        self._context_initial = (self._office.isChecked(), self._note.toPlainText())
        self._feedback.setText("Daily context saved.")
        self._context_deferred = False
        self.refresh()

    def _review_date_change(self) -> None:
        self._context_deferred = False
        self._sync_context_date(self._service.today_summary().work_date)

    def _sync_context_date(self, today: date) -> None:
        if self._context_editing:
            return
        pending = today != self._context_date
        self._change_date.setVisible(pending)
        if not pending:
            return
        dirty = self._context_initial != (self._office.isChecked(), self._note.toPlainText())
        if dirty:
            if self._context_deferred or not self.isVisible():
                return
            self._context_deferred = True
            answer = QMessageBox.question(
                self,
                "Daily context date changed",
                f"Save changes for {self._context_date:%d/%m/%Y} before opening today's context?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer == QMessageBox.StandardButton.Cancel:
                return
            if answer == QMessageBox.StandardButton.Save:
                self._save_day_context()
                return
        self._context_date = today
        self._context_deferred = False
        self._load_day_context()
        self._change_date.setVisible(False)

    def _run(self, action: Callable[[], object], success: str = "") -> None:
        try:
            action()
        except (DomainError, ValueError) as error:
            QMessageBox.warning(self, "QI Flow", str(error))
        else:
            self._feedback.setText(success)
        self.refresh()

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
        message.setDefaultButton(decide_later)
        message.exec()
        if message.clickedButton() is include:
            self._service.resolve_sleep_gap("include")
        elif message.clickedButton() is exclude:
            self._service.resolve_sleep_gap("exclude")
        else:
            # Choosing Decide later or closing the prompt must leave the unresolved
            # interval pending without reopening a modal dialog every timer tick.
            self._sleep_deferred = True
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
            self.open_timesheet_requested.emit()
        self.refresh()
