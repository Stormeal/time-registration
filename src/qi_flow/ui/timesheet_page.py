"""Month and ISO-week review screen for US12 and US13."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from qi_flow.application.dto import DaySummaryView
from qi_flow.application.testhuset import TesthusetService
from qi_flow.application.time_tracking import TimeTrackingApplicationService
from qi_flow.domain.models import IsoWeek
from qi_flow.domain.testhuset import decimal_hours
from qi_flow.domain.time_rules import COPENHAGEN
from qi_flow.ui.formatting import format_duration
from qi_flow.ui.manual_entry_dialog import ManualEntryDialog
from qi_flow.ui.session_editor_dialog import SessionEditorDialog
from qi_flow.ui.testhuset_dialog import SheetFactory, TesthusetDialog


class TimesheetPage(QWidget):
    """Shows every local date in the selected month, grouped by ISO week."""

    _COLUMNS = (
        "Date",
        "Start",
        "Finish",
        "Sessions",
        "Lunch",
        "Net",
        "Decimal hours",
        "Office",
        "Note",
    )

    def __init__(
        self,
        service: TimeTrackingApplicationService,
        testhuset: TesthusetService | None = None,
        sheet_factory: SheetFactory | None = None,
    ) -> None:
        super().__init__()
        self._service = service
        self._testhuset = testhuset
        self._sheet_factory = sheet_factory
        today = datetime.now(COPENHAGEN).date()
        self._year, self._month = today.year, today.month
        self._selected_week: IsoWeek | None = None
        self._selected_date: datetime | None = None

        self._month_label = QLabel()
        title_font = self._month_label.font()
        title_font.setPointSize(18)
        title_font.setBold(True)
        self._month_label.setFont(title_font)
        previous = QPushButton("Previous month")
        next_month = QPushButton("Next month")
        previous.clicked.connect(lambda: self._change_month(-1))
        next_month.clicked.connect(lambda: self._change_month(1))

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(self._COLUMNS)
        self._tree.setRootIsDecorated(True)
        self._tree.itemSelectionChanged.connect(self._select_day)
        self._tree.itemDoubleClicked.connect(self._open_item_editor)

        self._week_summary = QLabel("Select a day to review its ISO week.")
        self._target_hours = QSpinBox()
        self._target_hours.setRange(0, 100)
        self._target_hours.setSuffix(" hours weekly target")
        self._target_hours.setEnabled(False)
        self._target_hours.valueChanged.connect(self._save_target)
        self._add_entry = QPushButton("Add or correct entry")
        self._add_entry.clicked.connect(self._open_entry_dialog)
        self._edit_sessions = QPushButton("Edit sessions")
        self._edit_sessions.setEnabled(False)
        self._edit_sessions.clicked.connect(self._open_session_editor)

        navigation = QHBoxLayout()
        navigation.addWidget(previous)
        navigation.addWidget(self._month_label, 1)
        navigation.addWidget(next_month)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.addLayout(navigation)
        layout.addWidget(self._tree, 1)
        layout.addWidget(self._week_summary)
        layout.addWidget(self._target_hours)
        layout.addWidget(self._add_entry)
        layout.addWidget(self._edit_sessions)
        self._testhuset_button = QPushButton("Preview Testhuset week")
        self._testhuset_button.setEnabled(False)
        self._testhuset_button.clicked.connect(self._open_testhuset)
        if testhuset is not None and sheet_factory is not None:
            layout.addWidget(self._testhuset_button)
        self.refresh()

    def refresh(self) -> None:
        """Rebuild the grouped list from persisted timesheet summaries."""
        self._month_label.setText(datetime(self._year, self._month, 1).strftime("%B %Y"))
        self._tree.clear()
        groups: dict[IsoWeek, QTreeWidgetItem] = {}
        for summary in self._service.month(self._year, self._month):
            iso_year, iso_number, _ = summary.work_date.isocalendar()
            iso_week = IsoWeek(iso_year, iso_number)
            group = groups.get(iso_week)
            if group is None:
                group = QTreeWidgetItem([f"Week {iso_number}", "", "", "", "", "", "", ""])
                group.setData(0, Qt.ItemDataRole.UserRole, iso_week)
                group.setFirstColumnSpanned(True)
                self._tree.addTopLevelItem(group)
                groups[iso_week] = group
            group.addChild(self._day_item(summary))
        self._tree.expandAll()
        self._tree.resizeColumnToContents(0)
        if self._selected_week is not None:
            self._show_week(self._selected_week)

    def _day_item(self, summary: DaySummaryView) -> QTreeWidgetItem:
        suffix = " (provisional)" if summary.is_provisional else ""
        item = QTreeWidgetItem(
            [
                f"{summary.work_date:%d/%m/%Y}{suffix}",
                self._time(summary.first_start),
                self._time(summary.final_finish),
                str(summary.session_count),
                format_duration(summary.lunch_seconds)[:5],
                format_duration(summary.net_seconds)[:5],
                decimal_hours(summary.net_seconds),
                "Office" if summary.location and summary.location.value == "office" else "",
                "●" if summary.has_note else "",
            ]
        )
        item.setData(0, Qt.ItemDataRole.UserRole, summary)
        return item

    def _select_day(self) -> None:
        selected = self._tree.selectedItems()
        if not selected:
            return
        summary = selected[0].data(0, Qt.ItemDataRole.UserRole)
        if isinstance(summary, DaySummaryView):
            self._selected_date = datetime.combine(summary.work_date, datetime.min.time())
            self._edit_sessions.setEnabled(True)
            iso_year, iso_number, _ = summary.work_date.isocalendar()
            self._show_week(IsoWeek(iso_year, iso_number))
        elif isinstance(summary, IsoWeek):
            self._selected_date = None
            self._edit_sessions.setEnabled(False)
            self._show_week(summary)

    def _show_week(self, iso_week: IsoWeek) -> None:
        self._selected_week = iso_week
        self._testhuset_button.setEnabled(True)
        progress = self._service.weekly_progress(iso_week)
        decimal = progress.logged_seconds / 3600
        difference = progress.difference_seconds
        relation = "remaining" if difference < 0 else "excess"
        self._week_summary.setText(
            f"Week {iso_week.week}: {format_duration(progress.logged_seconds)[:5]} "
            f"({decimal:.2f} hours); {format_duration(abs(difference))[:5]} {relation}."
        )
        self._target_hours.blockSignals(True)
        self._target_hours.setValue(progress.target_minutes // 60)
        self._target_hours.blockSignals(False)
        self._target_hours.setEnabled(True)

    def _save_target(self, hours: int) -> None:
        if self._selected_week is not None:
            self._show_week(
                self._service.set_weekly_target(self._selected_week, hours * 60).iso_week
            )

    def _open_entry_dialog(self) -> None:
        work_date = self._selected_date.date() if self._selected_date is not None else None
        dialog = ManualEntryDialog(self._service, work_date)
        dialog.exec()
        self.refresh()

    def _open_item_editor(self, item: QTreeWidgetItem, _column: int) -> None:
        summary = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(summary, DaySummaryView):
            return
        self._selected_date = datetime.combine(summary.work_date, datetime.min.time())
        self._edit_sessions.setEnabled(True)
        self._open_session_editor()

    def _open_session_editor(self) -> None:
        if self._selected_date is None:
            return
        dialog = SessionEditorDialog(self._service, self._selected_date.date(), self._testhuset)
        dialog.exec()
        self.refresh()

    def _open_testhuset(self) -> None:
        if self._selected_week and self._testhuset and self._sheet_factory:
            dialog = TesthusetDialog(self._testhuset, self._sheet_factory, self._selected_week)
            dialog.exec()

    def _change_month(self, offset: int) -> None:
        target = self._year * 12 + self._month - 1 + offset
        self._year, month_index = divmod(target, 12)
        self._month = month_index + 1
        self._selected_week = None
        self._selected_date = None
        self._testhuset_button.setEnabled(False)
        self._edit_sessions.setEnabled(False)
        self._week_summary.setText("Select a day to review its ISO week.")
        self._target_hours.setEnabled(False)
        self.refresh()

    @staticmethod
    def _time(value: datetime | None) -> str:
        return value.astimezone(COPENHAGEN).strftime("%H:%M") if value is not None else ""
