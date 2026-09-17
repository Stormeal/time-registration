"""Explicit exit confirmation for the tray's Close app command (US10, D031)."""

from __future__ import annotations

from contextlib import suppress
from enum import Enum, auto

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtWidgets import QMessageBox, QWidget

from qi_flow.application.dto import FinishWorkCommand
from qi_flow.application.time_tracking import TimeTrackingApplicationService
from qi_flow.domain.errors import DomainError


class ExitChoice(Enum):
    """The user's explicit answer to the close-app confirmation."""

    CANCEL = auto()
    KEEP_RUNNING = auto()
    FINISH_AND_CLOSE = auto()


class ExitCoordinator(QObject):
    """Own the Close app confirmation so a running session is never silently ended.

    With no active session, ``request_exit`` confirms immediately (D031). With one running, it
    asks explicitly; Cancel is the default button. While lunch is active, "Finish work and
    close" is unavailable rather than ending lunch on the user's behalf (US10) -- the user
    cancels, ends lunch from QI Flow, and closes again.
    """

    exit_confirmed = Signal()

    def __init__(
        self,
        service: TimeTrackingApplicationService,
        parent_widget: QWidget | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._parent_widget = parent_widget

    @Slot()
    def request_exit(self) -> None:
        state = self._service.active_state()
        if state.session_id is None:
            self.exit_confirmed.emit()
            return

        lunch_active = state.active_deduction_kind is not None
        choice = self.ask(lunch_active)
        if choice is ExitChoice.CANCEL:
            return
        if choice is ExitChoice.FINISH_AND_CLOSE:
            # Defensive only: the option is disabled whenever finishing would be invalid.
            with suppress(DomainError):
                self._service.finish_work(FinishWorkCommand())
        self.exit_confirmed.emit()

    def ask(self, lunch_active: bool) -> ExitChoice:
        """Show the confirmation dialog. Overridable so tests can stub the modal prompt."""
        box = self._build_dialog(lunch_active)
        box.exec()
        return self._choice_for(box, box.clickedButton())

    def _build_dialog(self, lunch_active: bool) -> QMessageBox:
        """Construct the confirmation dialog without showing it (kept testable without exec())."""
        box = QMessageBox(self._parent_widget)
        box.setWindowTitle("Close QI Flow")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(self._message(lunch_active))

        box.addButton("Keep running and close", QMessageBox.ButtonRole.AcceptRole)
        if not lunch_active:
            box.addButton("Finish work and close", QMessageBox.ButtonRole.DestructiveRole)
        cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        return box

    @staticmethod
    def _choice_for(box: QMessageBox, clicked: object) -> ExitChoice:
        for button in box.buttons():
            role = box.buttonRole(button)
            if button is not clicked:
                continue
            if role == QMessageBox.ButtonRole.AcceptRole:
                return ExitChoice.KEEP_RUNNING
            if role == QMessageBox.ButtonRole.DestructiveRole:
                return ExitChoice.FINISH_AND_CLOSE
        return ExitChoice.CANCEL

    @staticmethod
    def _message(lunch_active: bool) -> str:
        message = "QI Flow is tracking an active work session."
        if lunch_active:
            message += (
                " Lunch is running, so it can't be finished from here"
                " -- cancel, end lunch in QI Flow, and close again."
            )
        message += (
            " Keeping it running pauses reminders until QI Flow reopens;"
            " the session itself is unaffected."
        )
        return message
