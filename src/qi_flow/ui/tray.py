"""Windows system-tray lifecycle adapter (US09, US11, D033-D035)."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from qi_flow.application.dto import (
    FinishDeductionCommand,
    FinishWorkCommand,
    StartDeductionCommand,
    StartWorkCommand,
)
from qi_flow.application.time_tracking import TimeTrackingApplicationService
from qi_flow.domain.errors import DomainError
from qi_flow.domain.models import DeductionKind
from qi_flow.infrastructure.startup import StartupManager
from qi_flow.ui.formatting import format_duration
from qi_flow.ui.manual_entry_dialog import ManualEntryDialog
from qi_flow.ui.reminder_dialog import ReminderDialog
from qi_flow.ui.tray_panel import TrayPanel


class TrayController(QObject):
    """Own the tray icon, its compact popover, and the right-click menu.

    Left-clicking the icon opens the compact ``TrayPanel`` (US09). The context menu mirrors the
    same live tracking state plus Start with Windows and Settings (D034). Both stay in sync with
    the main window because every surface reads the same application service.
    """

    open_requested = Signal()
    open_timesheet_requested = Signal()
    settings_requested = Signal()
    close_app_requested = Signal()

    def __init__(
        self,
        icon: QIcon,
        service: TimeTrackingApplicationService,
        startup_manager: StartupManager,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._startup_manager = startup_manager
        self._panel = TrayPanel(service, self.open_timesheet_requested.emit)
        self._reminder_dialogs: list[ReminderDialog] = []

        self._tray = QSystemTrayIcon(icon, self)
        self._tray.setToolTip("QI Flow")

        self._menu = QMenu()
        open_action = QAction("Open QI Flow", self._menu)
        open_action.triggered.connect(self.open_requested.emit)
        self._menu.addAction(open_action)

        self._state_action = QAction("", self._menu)
        self._state_action.setEnabled(False)
        self._menu.addAction(self._state_action)
        self._menu.addSeparator()

        self._start_work_action = QAction("Start work", self._menu)
        self._start_work_action.triggered.connect(self._start_work)
        self._menu.addAction(self._start_work_action)

        self._lunch_action = QAction("Start lunch", self._menu)
        self._lunch_action.triggered.connect(self._toggle_lunch)
        self._menu.addAction(self._lunch_action)

        self._finish_action = QAction("Finish work", self._menu)
        self._finish_action.triggered.connect(self._finish_work)
        self._menu.addAction(self._finish_action)
        self._menu.addSeparator()

        add_entry_action = QAction("Add entry", self._menu)
        add_entry_action.triggered.connect(self._add_manual_entry)
        self._menu.addAction(add_entry_action)

        self._startup_action = QAction("Start with Windows", self._menu)
        self._startup_action.setCheckable(True)
        self._startup_action.toggled.connect(self._set_start_with_windows)
        self._menu.addAction(self._startup_action)

        settings_action = QAction("Settings", self._menu)
        settings_action.triggered.connect(self.settings_requested.emit)
        self._menu.addAction(settings_action)
        self._menu.addSeparator()

        close_action = QAction("Close app", self._menu)
        close_action.triggered.connect(self.close_app_requested.emit)
        self._menu.addAction(close_action)

        self._menu.aboutToShow.connect(self._refresh_menu)
        self._tray.setContextMenu(self._menu)
        self._tray.activated.connect(self._on_activated)

        self._tooltip_timer = QTimer(self)
        self._tooltip_timer.setInterval(1000)
        self._tooltip_timer.timeout.connect(self._refresh_tooltip)
        self._tooltip_timer.start()
        self._refresh_tooltip()

        self._reminder_timer = QTimer(self)
        self._reminder_timer.setInterval(60_000)
        self._reminder_timer.timeout.connect(self._check_reminders)
        self._reminder_timer.start()
        self._check_reminders()

    def show(self) -> None:
        self._tray.show()

    def hide(self) -> None:
        self._panel.hide()
        self._tray.hide()

    @Slot(QSystemTrayIcon.ActivationReason)
    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._show_panel()

    def _show_panel(self) -> None:
        geometry = self._tray.geometry()
        anchor = (geometry.x(), geometry.y(), geometry.width(), geometry.height())
        self._panel.show_near(anchor)

    def _refresh_tooltip(self) -> None:
        state = self._service.active_state()
        if state.session_id is None:
            self._tray.setToolTip("QI Flow - not tracking")
        elif state.active_deduction_kind is not None:
            self._tray.setToolTip(f"QI Flow - lunch running ({format_duration(state.net_seconds)})")
        else:
            self._tray.setToolTip(f"QI Flow - working ({format_duration(state.net_seconds)})")

    def _refresh_menu(self) -> None:
        state = self._service.active_state()
        blocked = (
            self._service.recovery_state() is not None
            or self._service.pending_sleep_gap() is not None
        )
        if state.session_id is None:
            self._state_action.setText("Not tracking")
            self._start_work_action.setEnabled(not blocked)
            self._lunch_action.setEnabled(False)
            self._lunch_action.setText("Start lunch")
            self._finish_action.setEnabled(False)
        elif state.active_deduction_kind is not None:
            self._state_action.setText(f"Lunch running - {format_duration(state.net_seconds)}")
            self._start_work_action.setEnabled(False)
            self._lunch_action.setEnabled(not blocked)
            self._lunch_action.setText("End lunch")
            self._finish_action.setEnabled(False)
        else:
            self._state_action.setText(f"Working - {format_duration(state.net_seconds)}")
            self._start_work_action.setEnabled(False)
            self._lunch_action.setEnabled(not blocked)
            self._lunch_action.setText("Start lunch")
            self._finish_action.setEnabled(not blocked)

        self._startup_action.blockSignals(True)
        self._startup_action.setChecked(self._startup_manager.is_enabled())
        self._startup_action.blockSignals(False)

    def _check_reminders(self) -> None:
        for reminder in self._service.due_reminders():
            label = "Lunch" if reminder.kind == "lunch" else "Work"
            self._tray.showMessage(
                "QI Flow reminder",
                f"{label}: {format_duration(reminder.elapsed_seconds)[:5]}. "
                f"Net: {format_duration(reminder.net_seconds)[:5]}",
                QSystemTrayIcon.MessageIcon.Information,
            )

            def snooze(minutes: int, kind: str = reminder.kind) -> None:
                self._service.snooze_reminder(kind, minutes)

            dialog = ReminderDialog(
                reminder,
                self.open_requested.emit,
                snooze,
            )
            dialog.finished.connect(lambda _result, current=dialog: self._close_reminder(current))
            self._reminder_dialogs.append(dialog)
            dialog.show()

    def _close_reminder(self, dialog: ReminderDialog) -> None:
        if dialog in self._reminder_dialogs:
            self._reminder_dialogs.remove(dialog)
        dialog.deleteLater()

    def _start_work(self) -> None:
        self._run(lambda: self._service.start_work(StartWorkCommand()))

    def _toggle_lunch(self) -> None:
        state = self._service.active_state()
        if state.active_deduction_kind is None:
            self._run(
                lambda: self._service.start_deduction(StartDeductionCommand(DeductionKind.LUNCH))
            )
        else:
            self._run(lambda: self._service.finish_deduction(FinishDeductionCommand()))

    def _finish_work(self) -> None:
        self._run(lambda: self._service.finish_work(FinishWorkCommand()))

    def _add_manual_entry(self) -> None:
        dialog = ManualEntryDialog(self._service)
        dialog.exec()

    def _set_start_with_windows(self, enabled: bool) -> None:
        try:
            self._startup_manager.set_enabled(enabled)
        except RuntimeError as error:
            self._startup_action.blockSignals(True)
            self._startup_action.setChecked(not enabled)
            self._startup_action.blockSignals(False)
            self._tray.showMessage("QI Flow", str(error), QSystemTrayIcon.MessageIcon.Warning)

    def _run(self, action: Callable[[], object]) -> None:
        try:
            action()
        except DomainError as error:
            self._tray.showMessage("QI Flow", str(error), QSystemTrayIcon.MessageIcon.Warning)
