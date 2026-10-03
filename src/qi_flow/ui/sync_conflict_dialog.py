"""Explicit conflict choices with local, incoming and causal base versions."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from qi_flow.application.sync_models import EntityKey, SyncConflictReview
from qi_flow.domain.errors import DomainError
from qi_flow.domain.time_rules import COPENHAGEN
from qi_flow.ui.date_time_input import DateTimeInput


@dataclass(frozen=True)
class _Choice:
    payload: Mapping[str, object] | None


def _summary(payload: Mapping[str, object] | None) -> str:
    if payload is None:
        return "Deleted or absent"
    if "actual_started_at" in payload:
        stamps = []
        for field in ("actual_started_at", "actual_ended_at"):
            value = payload.get(field)
            try:
                instant = datetime.fromisoformat(value) if isinstance(value, str) else None
                stamps.append(
                    instant.astimezone(COPENHAGEN).strftime("%d/%m/%Y %H:%M %Z")
                    if instant is not None and instant.utcoffset() is not None
                    else "Missing time"
                )
            except (ValueError, OverflowError):
                stamps.append("Unrecognized date or time")
        attributes = [
            str(payload[field])
            for field in ("kind", "testhuset_task_id", "dsb_allocation_id")
            if payload.get(field) is not None
        ]
        return " to ".join(stamps) + ("; " + ", ".join(attributes) if attributes else "")
    return f"{payload.get('location', '')}: {payload.get('note', '')}"


def _label(text: str) -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


class SyncConflictDialog(QDialog):
    resolution_requested = Signal(str, object, object)

    def __init__(self, review: SyncConflictReview, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Review sync conflict")
        self._review = review
        self._busy = False
        self.choices: dict[EntityKey, QComboBox] = {}
        self.corrections: dict[EntityKey, tuple[QCheckBox, DateTimeInput, DateTimeInput]] = {}
        layout = QVBoxLayout(self)
        layout.addWidget(
            _label(
                "Choose a version or deletion for every affected entry. "
                "The complete selection must form a valid timesheet. "
                "New local or shared changes will require a fresh review."
            )
        )
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        entries = QVBoxLayout(content)
        for key in review.conflict.entity_keys:
            title = {
                "work_session": "Work",
                "deduction": "Lunch or break",
                "day_details": "Day details",
            }.get(key[0], "Entry")
            group = QGroupBox(title + " · " + key[1])
            form = QFormLayout(group)
            local = review.local_payloads[key]
            form.addRow("Local", _label(_summary(local)))
            for base in review.bases:
                if base.entity_key == key:
                    form.addRow("Reviewed base", _label(_summary(base.payload)))
            combo = QComboBox()
            combo.setAccessibleName("Choose resolution for " + title + " " + key[1])
            combo.addItem("Choose a version", None)
            combo.addItem("Keep local version", _Choice(local))
            versions = [c for c in review.conflict.changes if c.entity_key == key]
            for index, change in enumerate(versions, 1):
                form.addRow(f"Version {index}", _label(_summary(change.payload)))
                combo.addItem(f"Use version {index}", _Choice(change.payload))
            combo.addItem("Delete this entry", _Choice(None))
            combo.currentIndexChanged.connect(self._refresh)
            form.addRow("Resolution", combo)
            self.choices[key] = combo
            if key[0] in {"work_session", "deduction"}:
                initial_date = review.conflict.changes[0].created_at.astimezone(COPENHAGEN).date()
                check = QCheckBox("Correct dates and times for this choice")
                start = DateTimeInput(initial_date, "Corrected start")
                end = DateTimeInput(initial_date, "Corrected finish")
                form.addRow(check)
                form.addRow("Start", start)
                form.addRow("Finish", end)
                self.corrections[key] = check, start, end
                check.toggled.connect(lambda _checked=False, key=key: self._refresh_correction(key))
                combo.currentIndexChanged.connect(
                    lambda _index=0, key=key: self._load_correction(key)
                )
                self._refresh_correction(key)
            entries.addWidget(group)
        scroll.setWidget(content)
        layout.addWidget(scroll)
        self.status_label = _label("No resolution has been chosen.")
        layout.addWidget(self.status_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.save_button = QPushButton("Save resolution")
        buttons.addButton(self.save_button, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.rejected.connect(self.reject)
        self.save_button.clicked.connect(self._save)
        layout.addWidget(buttons)
        self.resize(620, 600)
        self._refresh()

    def _refresh(self) -> None:
        self.save_button.setEnabled(
            not self._busy and all(c.currentIndex() > 0 for c in self.choices.values())
        )

    def _refresh_correction(self, key: EntityKey) -> None:
        check, start, end = self.corrections[key]
        choice = self.choices[key].currentData()
        eligible = isinstance(choice, _Choice) and choice.payload is not None and not self._busy
        check.setEnabled(eligible)
        start.setEnabled(eligible and check.isChecked())
        end.setEnabled(eligible and check.isChecked())

    def _load_correction(self, key: EntityKey) -> None:
        check, start, end = self.corrections[key]
        check.setChecked(False)
        choice = self.choices[key].currentData()
        if isinstance(choice, _Choice) and choice.payload is not None:
            for field, widget in (("actual_started_at", start), ("actual_ended_at", end)):
                value = choice.payload.get(field)
                try:
                    instant = datetime.fromisoformat(value) if isinstance(value, str) else None
                    if instant is not None and instant.utcoffset() is not None:
                        widget.set_value(instant)
                except (ValueError, OverflowError):
                    pass  # Invalid incoming history remains reviewable for correction/deletion.
        self._refresh_correction(key)

    def _save(self) -> None:
        payloads = {}
        for key, combo in self.choices.items():
            choice = combo.currentData()
            if not isinstance(choice, _Choice):
                return
            payload = dict(choice.payload) if choice.payload is not None else None
            payloads[key] = payload
            correction = self.corrections.get(key)
            if correction is not None and correction[0].isChecked() and payload is not None:
                try:
                    start, end = correction[1].utc_value(), correction[2].utc_value()
                except DomainError as error:
                    self.show_failure(str(error))
                    return
                payload.update(
                    actual_started_at=start.isoformat(),
                    actual_ended_at=end.isoformat(),
                    effective_started_at=start.isoformat(),
                    effective_ended_at=end.isoformat(),
                    source="manual",
                )
        self._busy = True
        for combo in self.choices.values():
            combo.setEnabled(False)
        for key in self.corrections:
            self._refresh_correction(key)
        self._refresh()
        self.resolution_requested.emit(
            self._review.conflict.conflict_id, self._review.conflict.head_ids, payloads
        )

    def show_failure(self, message: str) -> None:
        self.status_label.setText(message)
        self._busy = False
        for combo in self.choices.values():
            combo.setEnabled(True)
        for key in self.corrections:
            self._refresh_correction(key)
        self._refresh()
