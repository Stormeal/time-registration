"""Browse and restore recoverable entry versions for one local date."""

from __future__ import annotations

from datetime import date, datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from qi_flow.application.dto import EntryHistoryView
from qi_flow.application.time_tracking import TimeTrackingApplicationService
from qi_flow.domain.errors import DomainError
from qi_flow.domain.time_rules import COPENHAGEN


class HistoryDialog(QDialog):
    """Shows prior values without adding history noise to the regular timesheet."""

    def __init__(self, service: TimeTrackingApplicationService, work_date: date) -> None:
        super().__init__()
        self._service = service
        self.setWindowTitle(f"Recoverable history — {work_date:%d/%m/%Y}")
        self.resize(650, 360)
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(("Entry", "Action", "Previous interval", "Changed"))
        self._restore = QPushButton("Restore selected version")
        self._restore.setEnabled(False)
        self._restore.setToolTip("Select a saved entry version to restore.")
        self._tree.itemSelectionChanged.connect(self._update_restore_button)
        self._restore.clicked.connect(self._restore_selected)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addWidget(self._restore)
        buttons.addWidget(close)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Deleted entries and previous edited values are kept for 30 days."))
        layout.addWidget(self._tree, 1)
        layout.addLayout(buttons)
        self._load(work_date)

    def _update_restore_button(self) -> None:
        enabled = bool(self._tree.selectedItems())
        self._restore.setEnabled(enabled)
        self._restore.setToolTip("" if enabled else "Select a saved entry version to restore.")

    def _load(self, work_date: date) -> None:
        for record in self._service.entry_history_for_day(work_date):
            snapshot = record.before_state
            started = self._as_local(snapshot.get("actual_started_at"))
            ended = self._as_local(snapshot.get("actual_ended_at"))
            if record.entity_type == "deduction":
                kind = snapshot.get("kind")
                if kind == "lunch":
                    label = "Lunch"
                elif kind == "sleep_break":
                    label = "Sleep break"
                else:
                    label = "Lunch or break"
            else:
                label = "Work session"
            interval = f"{started} - {ended or 'running'}"
            changed = record.changed_at.astimezone(COPENHAGEN).strftime("%d/%m/%Y %H:%M")
            item = QTreeWidgetItem([label, record.action.title(), interval, changed])
            item.setData(0, Qt.ItemDataRole.UserRole, record)
            self._tree.addTopLevelItem(item)
        self._tree.resizeColumnToContents(0)

    def _restore_selected(self) -> None:
        selected = self._tree.selectedItems()
        if not selected:
            return
        record = selected[0].data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(record, EntryHistoryView):
            return
        answer = QMessageBox.question(
            self,
            "Restore previous version?",
            "Restore this version? Current values stay available in history for 30 days.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self._service.restore_history_entry(record.audit_id)
        except DomainError as error:
            QMessageBox.warning(self, "Could not restore entry", str(error))
            return
        self.accept()

    @staticmethod
    def _as_local(value: object) -> str:
        if not isinstance(value, str):
            return ""
        return datetime.fromisoformat(value).astimezone(COPENHAGEN).strftime("%d/%m/%Y %H:%M")
