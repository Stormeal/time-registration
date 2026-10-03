"""Guided migration review; composition owns workers and concrete adapters."""

from dataclasses import dataclass

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from qi_flow.application.sync_migration import MigrationPlan


@dataclass(frozen=True)
class MigrationRequest:
    action: str
    participants: tuple[str, ...]
    participant: str
    writers_paused: bool


class SyncMigrationDialog(QDialog):
    migration_requested = Signal(object)
    cancel_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Upgrade shared Sheets sync")
        self._busy = False
        self._plan: MigrationPlan | None = None
        self._acknowledged: tuple[str, ...] = ()
        self._completed = False
        layout = QVBoxLayout(self)
        explanation = QLabel(
            "Pause and upgrade QI Flow on every computer that writes to this Sheet. "
            "Older versions can replace shared history. The upgrade preserves the original "
            "Sheet and verifies a safety backup on each participating computer. "
            "Declare every computer below, then contribute its local history from that computer. "
            "Keep completed entries unchanged until cutover is verified. Differing histories "
            "will require conflict review."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        form = QFormLayout()
        self.roster_edit = QPlainTextEdit()
        self.roster_edit.setAccessibleName("Participating computers, one per line")
        self.participant_edit = QLineEdit()
        self.participant_edit.setAccessibleName("This computer's participant name")
        self.paused_check = QCheckBox("Every V1 writer is paused and upgraded")
        form.addRow("All participating computers", self.roster_edit)
        form.addRow("This computer", self.participant_edit)
        form.addRow(self.paused_check)
        layout.addLayout(form)
        self.review_button = QPushButton("Create or join migration")
        self.contribute_button = QPushButton("Back up and contribute this computer")
        self.complete_button = QPushButton("Verify cutover and enable sync")
        for button, action in (
            (self.review_button, "begin"),
            (self.contribute_button, "contribute"),
            (self.complete_button, "complete"),
        ):
            button.clicked.connect(lambda _checked=False, action=action: self._request(action))
            layout.addWidget(button)
        self.status_label = QLabel("Migration has not been reviewed.")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.roster_edit.textChanged.connect(self._refresh)
        self.participant_edit.textChanged.connect(self._refresh)
        self.paused_check.toggled.connect(self._refresh)
        self._refresh()

    def _roster(self) -> tuple[str, ...]:
        return tuple(
            line.strip() for line in self.roster_edit.toPlainText().splitlines() if line.strip()
        )

    def _refresh(self) -> None:
        roster = self._roster()
        ready = not self._busy and self.paused_check.isChecked()
        self.review_button.setEnabled(ready and bool(roster) and len(set(roster)) == len(roster))
        reviewed = self._plan is not None and set(roster) == set(self._plan.participants)
        participant = self.participant_edit.text().strip()
        eligible = ready and reviewed and participant in roster and not self._completed
        self.contribute_button.setEnabled(eligible)
        self.complete_button.setEnabled(eligible and set(self._acknowledged) == set(roster))

    def _request(self, action: str) -> None:
        request = MigrationRequest(
            action,
            self._roster(),
            self.participant_edit.text().strip(),
            self.paused_check.isChecked(),
        )
        self.set_busy(True)
        self.migration_requested.emit(request)

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        for field in (self.roster_edit, self.participant_edit, self.paused_check):
            field.setEnabled(not busy)
        self._refresh()

    def show_status(
        self, plan: MigrationPlan, acknowledged: tuple[str, ...], *, completed: bool
    ) -> None:
        self._plan, self._acknowledged, self._completed = plan, acknowledged, completed
        self.roster_edit.setPlainText("\n".join(plan.participants))
        missing = sorted(set(plan.participants) - set(acknowledged))
        self.status_label.setText(
            "Cutover verified. Review any conflicts before publication."
            if completed
            else "Waiting for verified snapshots: " + ", ".join(missing)
            if missing
            else "Every participant snapshot is verified. Cutover can now be checked."
        )
        self.set_busy(False)

    def show_failure(self, message: str) -> None:
        self.status_label.setText(message)
        self.set_busy(False)

    def reject(self) -> None:
        if self._busy:
            self.cancel_requested.emit()
        super().reject()
