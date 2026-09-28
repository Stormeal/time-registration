"""A focused daily-note editor with explicit save and discard confirmation."""

from datetime import date

from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class DailyNoteDialog(QDialog):
    def __init__(self, work_date: date, note: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Daily note — {work_date:%d/%m/%Y}")
        self.resize(520, 360)
        self._initial = note
        self._note = QTextEdit()
        self._note.setAccessibleName("Daily note")
        self._note.setPlaceholderText("Write a note for this day…")
        self._note.setPlainText(note)
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.addWidget(QLabel(f"Note for {work_date:%d/%m/%Y}"))
        layout.addWidget(self._note, 1)
        layout.addWidget(self._buttons)

    def note(self) -> str:
        return self._note.toPlainText()

    def reject(self) -> None:
        if self.note() != self._initial:
            answer = QMessageBox.question(
                self,
                "Discard note changes?",
                "Discard the unsaved changes to this note?",
                QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Discard:
                return
        super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:
        event.ignore()
        self.reject()
