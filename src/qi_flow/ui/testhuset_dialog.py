"""Responsive temporary-browser workflow with an explicit per-slot confirmation."""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from threading import Event

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from qi_flow.application.dsb import DsbPreview
from qi_flow.application.testhuset import (
    FillDecision,
    FillDecisions,
    FillPreview,
    TesthusetService,
    WeeklySheet,
)
from qi_flow.domain.models import IsoWeek
from qi_flow.domain.testhuset import decimal_hours
from qi_flow.ui.runtime_lifecycle import ShutdownGroup

SheetFactory = Callable[[Event, Callable[[str], None]], AbstractContextManager[WeeklySheet]]
_LOG = logging.getLogger(__name__)


class TesthusetWorker(QThread):
    __test__ = False
    status = Signal(str)
    preview_ready = Signal(object)
    outcome = Signal(str)

    def __init__(
        self,
        service: TesthusetService,
        factory: SheetFactory,
        week: IsoWeek,
        scan_only: bool,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.service, self.factory, self.week = service, factory, week
        self.scan_only = scan_only
        self.cancelled = Event()
        self.decision = Event()
        self.choices: FillDecisions = {}
        self.confirmed = False

    def run(self) -> None:
        fill_started = False
        try:
            with self.factory(self.cancelled, self.status.emit) as sheet:
                if self.scan_only:
                    tasks = self.service.scan(sheet, self.week)
                    message = f"Scanned {len(tasks)} tasks. Choose your default task in Settings."
                else:
                    preview = self.service.preview(sheet, self.week)
                    self.preview_ready.emit(preview)
                    while not self.decision.wait(0.2):
                        if self.cancelled.is_set():
                            break
                    if self.cancelled.is_set() or not self.confirmed:
                        self.outcome.emit("Cancelled. No fill was started.")
                        return
                    self.status.emit("Verifying the preview and saving confirmed slots…")
                    fill_started = True
                    result = self.service.fill(sheet, preview, self.choices, confirmed=True)
                    message = (
                        f"Verified {result.changed} changed slots; kept {result.kept}; "
                        f"{result.matched} already matched. Closing the week remains a manual "
                        f"{self.service.destination} action."
                    )
            self.outcome.emit(message)
        except ValueError as error:
            message = str(error)
            if fill_started:
                message += " Prepare a new preview before filling again."
            self.outcome.emit(message)
        except Exception as error:
            # Do not expose external/browser errors or work contents in diagnostics.
            _LOG.warning("%s operation failed (%s)", self.service.destination, type(error).__name__)
            if fill_started:
                self.outcome.emit(
                    f"{self.service.destination} fill stopped; some saves may have completed. "
                    "Prepare a new preview to review destination values before filling again."
                )
            else:
                self.outcome.emit(
                    f"{self.service.destination} operation failed before a preview could be "
                    "prepared. Check the QI Flow diagnostic log for the failure type, then rescan."
                )


class TesthusetDialog(QDialog):
    __test__ = False

    def __init__(
        self,
        service: TesthusetService,
        factory: SheetFactory,
        week: IsoWeek,
        *,
        scan_only: bool = False,
        shutdown: ShutdownGroup | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle(f"{service.destination} — week {week.week}, {week.year}")
        self.resize(900, 520)
        self._status = QLabel("Opening a temporary browser…")
        self._status.setWordWrap(True)
        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(
            ("Date", "Project / task", "QI Flow hours", f"{service.destination} hours", "Decision")
        )
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._coverage_status = QLabel()
        self._coverage_status.setWordWrap(True)
        self._coverage_table = QTableWidget(0, 4)
        self._coverage_table.setHorizontalHeaderLabels(
            ("Date", "Testhuset branch", "Net hours", "DSB inclusion")
        )
        self._coverage_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._coverage_status.hide()
        self._coverage_table.hide()
        self._fill = QPushButton(f"Fill {service.destination} timesheet")
        self._fill.setEnabled(False)
        self._fill.setToolTip("The weekly review is loading.")
        self._fill.clicked.connect(self._confirm)
        self._cancel = QPushButton("Cancel")
        self._cancel.clicked.connect(self.reject)
        self._choices: dict[int, QComboBox] = {}
        self._worker = TesthusetWorker(service, factory, week, scan_only, self)
        self._worker.status.connect(self._status.setText)
        self._worker.preview_ready.connect(self._preview)
        self._worker.outcome.connect(self._outcome)
        self._worker.finished.connect(self._finished)
        buttons = QHBoxLayout()
        buttons.addWidget(self._fill)
        buttons.addWidget(self._cancel)
        layout = QVBoxLayout(self)
        layout.addWidget(self._status)
        layout.addWidget(self._coverage_status)
        layout.addWidget(self._coverage_table)
        layout.addWidget(self._table)
        layout.addLayout(buttons)

        def cancel() -> None:
            self._worker.cancelled.set()
            self._worker.decision.set()

        if shutdown is None or shutdown.track_thread(self._worker, cancel):
            self._worker.start()

    def _preview(self, preview: FillPreview) -> None:
        if isinstance(preview, DsbPreview):
            coverage = preview.coverage
            self._coverage_status.setText(
                f"Included DSB hours: {decimal_hours(coverage.included_seconds)}. "
                f"Excluded hours: {decimal_hours(coverage.excluded_seconds)}. "
                "Review exclusions and unresolved assignments before sending."
            )
            self._coverage_table.setRowCount(len(coverage.entries))
            for row, entry in enumerate(coverage.entries):
                values = (
                    entry.work_date.strftime("%d/%m/%Y"),
                    entry.branch.label
                    if entry.branch
                    else f"Unresolved assignment ({entry.branch_id or 'none'})",
                    decimal_hours(entry.seconds),
                    "Included" if entry.included else "Excluded",
                )
                for column, value in enumerate(values):
                    self._coverage_table.setItem(row, column, QTableWidgetItem(value))
            self._coverage_table.resizeColumnsToContents()
            self._coverage_status.show()
            self._coverage_table.show()
        self._status.setText(
            "Review every differing slot, then confirm the fill. Only listed slots are affected. "
            "Closing the week remains manual."
        )
        self._choices.clear()
        self._table.setRowCount(len(preview.slots))
        for row, item in enumerate(preview.slots):
            for column, value in enumerate(
                (
                    item.proposed.work_date.strftime("%d/%m/%Y"),
                    item.proposed.task.label,
                    item.proposed.hours,
                    item.existing or "0",
                )
            ):
                self._table.setItem(row, column, QTableWidgetItem(value))
            if item.matches:
                self._table.setItem(row, 4, QTableWidgetItem("Already matches"))
            else:
                choice = QComboBox()
                choice.addItem("Choose Keep or Replace...", None)
                choice.addItem("Replace with QI Flow value", FillDecision.REPLACE)
                choice.addItem(f"Keep {self._worker.service.destination} value", FillDecision.KEEP)
                choice.currentIndexChanged.connect(self._validate_choices)
                self._choices[row] = choice
                self._table.setCellWidget(row, 4, choice)
        self._table.resizeColumnsToContents()
        self._validate_choices()

    def _validate_choices(self) -> None:
        can_fill = self._table.rowCount() > 0 and all(
            isinstance(choice.currentData(), FillDecision) for choice in self._choices.values()
        )
        self._fill.setEnabled(can_fill)
        self._fill.setToolTip(
            "" if can_fill else "Choose whether to keep or replace each differing existing value."
        )

    def _confirm(self) -> None:
        if not self._fill.isEnabled():
            return
        self._fill.setEnabled(False)
        self._fill.setToolTip("The confirmed timesheet fill is running.")
        self._table.setEnabled(False)
        self._worker.choices = {row: choice.currentData() for row, choice in self._choices.items()}
        self._worker.confirmed = True
        self._worker.decision.set()

    def _outcome(self, message: str) -> None:
        self._status.setText(message)
        self._fill.setEnabled(False)
        self._fill.setToolTip(message)

    def _finished(self) -> None:
        self._cancel.setText("Close")
        self._cancel.setEnabled(True)

    def reject(self) -> None:
        if self._worker.isRunning():
            self._worker.cancelled.set()
            self._worker.decision.set()
            self._fill.setEnabled(False)
            self._table.setEnabled(False)
            self._cancel.setEnabled(False)
            self._status.setText("Cancelling and closing the temporary browser…")
            return
        super().reject()
