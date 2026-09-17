"""Non-modal reminder choice dialog used while the application is running."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QPushButton, QVBoxLayout, QWidget

from qi_flow.application.dto import ReminderView
from qi_flow.ui.formatting import format_duration


class ReminderDialog(QDialog):
    """Offer the confirmed open and snooze choices without changing timer state."""

    def __init__(
        self,
        reminder: ReminderView,
        open_app: Callable[[], None],
        snooze: Callable[[int], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("QI Flow reminder")
        kind = "Lunch" if reminder.kind == "lunch" else "Work"
        message = QLabel(
            f"{kind} has reached {format_duration(reminder.elapsed_seconds)[:5]}. "
            f"Net work: {format_duration(reminder.net_seconds)[:5]}."
        )
        message.setWordWrap(True)
        buttons = QDialogButtonBox()
        open_button = QPushButton("Open QI Flow")
        buttons.addButton(open_button, QDialogButtonBox.ButtonRole.AcceptRole)
        for minutes in (15, 30, 60):
            button = QPushButton(f"Snooze {minutes} min")
            button.clicked.connect(
                lambda _checked=False, value=minutes: self._snooze(snooze, value)
            )
            buttons.addButton(button, QDialogButtonBox.ButtonRole.ActionRole)
        open_button.clicked.connect(lambda: self._open(open_app))
        self.rejected.connect(lambda: self._snooze(snooze, 15))
        layout = QVBoxLayout(self)
        layout.addWidget(message)
        layout.addWidget(buttons)

    def _open(self, callback: Callable[[], None]) -> None:
        callback()
        self.accept()

    def _snooze(self, callback: Callable[[int], None], minutes: int) -> None:
        callback(minutes)
        self.accept()
