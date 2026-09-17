"""UI interaction tests for assignments and the explicit fill decision boundary."""

from contextlib import contextmanager
from datetime import UTC, date, datetime

from PySide6.QtCore import Qt, QTime

from qi_flow.application.dto import ManualWorkSessionCommand, UpdateDayDetailsCommand
from qi_flow.application.testhuset import TesthusetService
from qi_flow.application.time_tracking import TimeTrackingApplicationService
from qi_flow.domain.models import IsoWeek, WorkLocation
from qi_flow.domain.testhuset import ProjectTask
from qi_flow.domain.time_rules import COPENHAGEN
from qi_flow.infrastructure.sqlite.database import SQLiteDatabase
from qi_flow.infrastructure.sqlite.repositories import SQLiteUnitOfWork
from qi_flow.infrastructure.system import UuidIdentifierGenerator
from qi_flow.infrastructure.testhuset_cache import JsonTaskCache
from qi_flow.ui.manual_entry_dialog import ManualEntryDialog
from qi_flow.ui.session_editor_dialog import SessionEditorDialog
from qi_flow.ui.testhuset_dialog import TesthusetDialog
from qi_flow.ui.timesheet_page import TimesheetPage


class Clock:
    def now(self):
        return datetime(2026, 9, 17, tzinfo=UTC)


def build(tmp_path):
    database = SQLiteDatabase(tmp_path / "time.sqlite3")
    database.initialize()
    cache = JsonTaskCache(tmp_path / "testhuset-projects.json")
    task = ProjectTask("11-22", "Example", "Testing")
    cache.replace((task,))
    ids = UuidIdentifierGenerator()
    service = TesthusetService(lambda: SQLiteUnitOfWork(database), Clock(), ids, cache)
    service.set_default(task.id)
    tracking = TimeTrackingApplicationService(lambda: SQLiteUnitOfWork(database), Clock(), ids)
    session = tracking.add_manual_session(
        ManualWorkSessionCommand(
            datetime(2026, 9, 14, 7, tzinfo=UTC), datetime(2026, 9, 14, 14, 45, tzinfo=UTC)
        )
    )
    return service, tracking, session, task


def test_override_save_is_independent_of_time_correction(qtbot, tmp_path) -> None:
    service, tracking, session, task = build(tmp_path)
    dialog = SessionEditorDialog(tracking, date(2026, 9, 14), service)
    qtbot.addWidget(dialog)
    dialog._tree.setCurrentItem(dialog._tree.topLevelItem(0))
    dialog._task.setCurrentIndex(dialog._task.findData(task.id))
    qtbot.mouseClick(dialog._save_task, Qt.MouseButton.LeftButton)
    saved = tracking.completed_sessions_for_day(date(2026, 9, 14))[0]
    assert saved.testhuset_task_id == task.id
    assert saved.actual_started_at == session.actual_started_at
    assert saved.actual_ended_at == session.actual_ended_at


def test_decimal_column_and_iso_week_group_selection(qtbot, tmp_path) -> None:
    service, tracking, _, _ = build(tmp_path)
    page = TimesheetPage(tracking, service)
    qtbot.addWidget(page)
    page._year, page._month = 2026, 9
    page.refresh()
    group = next(
        page._tree.topLevelItem(i)
        for i in range(page._tree.topLevelItemCount())
        if page._tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) == IsoWeek(2026, 38)
    )
    page._tree.setCurrentItem(group)
    assert page._selected_week == IsoWeek(2026, 38)
    assert group.child(0).text(page._COLUMNS.index("Decimal hours")) == "7.75"


def test_double_clicking_a_day_opens_its_session_editor(qtbot, tmp_path, monkeypatch) -> None:
    service, tracking, _, _ = build(tmp_path)
    page = TimesheetPage(tracking, service)
    qtbot.addWidget(page)
    page._year, page._month = 2026, 9
    page.refresh()
    group = next(
        page._tree.topLevelItem(index)
        for index in range(page._tree.topLevelItemCount())
        if page._tree.topLevelItem(index).data(0, Qt.ItemDataRole.UserRole) == IsoWeek(2026, 38)
    )
    opened = []
    monkeypatch.setattr(page, "_open_session_editor", lambda: opened.append(page._selected_date))

    page._tree.itemDoubleClicked.emit(group.child(0), 0)

    assert opened == [datetime(2026, 9, 14)]


def test_manual_entry_uses_one_date_and_time_only_inputs(qtbot, tmp_path) -> None:
    _, tracking, _, _ = build(tmp_path)
    dialog = ManualEntryDialog(tracking, date(2026, 9, 14))
    qtbot.addWidget(dialog)

    assert dialog._date.date().toPython() == date(2026, 9, 14)
    assert dialog._start.displayFormat() == "HH:mm"
    assert dialog._end.displayFormat() == "HH:mm"
    assert dialog._as_copenhagen(dialog._date.date(), QTime(7, 30)) == datetime(
        2026, 9, 14, 7, 30, tzinfo=COPENHAGEN
    )


def test_session_editor_uses_the_selected_day_and_time_only_inputs(qtbot, tmp_path) -> None:
    service, tracking, _, _ = build(tmp_path)
    dialog = SessionEditorDialog(tracking, date(2026, 9, 14), service)
    qtbot.addWidget(dialog)
    session_item = dialog._tree.topLevelItem(0)
    dialog._tree.setCurrentItem(session_item)

    assert session_item.text(1) == "09:00"
    assert session_item.text(2) == "16:45"
    assert dialog._start.displayFormat() == "HH:mm"
    assert dialog._end.displayFormat() == "HH:mm"
    dialog._start.setTime(QTime(8, 0))
    dialog._end.setTime(QTime(15, 0))
    dialog._save_selected()

    saved = tracking.completed_sessions_for_day(date(2026, 9, 14))[0]
    assert saved.actual_started_at == datetime(2026, 9, 14, 6, tzinfo=UTC)
    assert saved.actual_ended_at == datetime(2026, 9, 14, 13, tzinfo=UTC)


def test_session_editor_can_save_office_status_for_its_day(qtbot, tmp_path) -> None:
    service, tracking, _, _ = build(tmp_path)
    tracking.update_day_details(
        UpdateDayDetailsCommand(date(2026, 9, 14), WorkLocation.REMOTE, "DSB office day")
    )
    dialog = SessionEditorDialog(tracking, date(2026, 9, 14), service)
    qtbot.addWidget(dialog)

    dialog._office.setChecked(True)
    qtbot.mouseClick(dialog._save_office, Qt.MouseButton.LeftButton)

    details = tracking.day_details(date(2026, 9, 14))
    assert details is not None
    assert details.location is WorkLocation.OFFICE
    assert details.note == "DSB office day"


def test_no_writes_before_fill_confirmation(qtbot, tmp_path) -> None:
    service, _, _, task = build(tmp_path)
    writes = []
    closed = []

    class Sheet:
        def scan(self, week):
            assert week == IsoWeek(2026, 38)
            return (task,)

        def read(self, slot):
            return "1,00"

        def write_verified(self, slot):
            writes.append(slot)

    @contextmanager
    def factory(cancelled, status):
        try:
            yield Sheet()
        finally:
            closed.append(True)

    dialog = TesthusetDialog(service, factory, IsoWeek(2026, 38))
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: bool(dialog._choices))
    assert writes == []
    assert dialog._fill.isEnabled()
    assert dialog._choices[0].currentData() is True
    assert writes == []
    qtbot.mouseClick(dialog._fill, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not dialog._worker.isRunning())
    assert len(writes) == 1
    assert closed == [True]


def test_preview_allows_keeping_a_differing_testhuset_value(qtbot, tmp_path) -> None:
    service, _, _, task = build(tmp_path)
    writes = []

    class Sheet:
        def scan(self, week):
            return (task,)

        def read(self, slot):
            return "1,00"

        def write_verified(self, slot):
            writes.append(slot)

    @contextmanager
    def factory(cancelled, status):
        yield Sheet()

    dialog = TesthusetDialog(service, factory, IsoWeek(2026, 38))
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: bool(dialog._choices))
    dialog._choices[0].setCurrentIndex(1)
    assert dialog._choices[0].currentData() is False
    qtbot.mouseClick(dialog._fill, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not dialog._worker.isRunning())
    assert writes == []


def test_cancel_preview_closes_temporary_session_without_writes(qtbot, tmp_path) -> None:
    service, _, _, task = build(tmp_path)
    closed = []

    class Sheet:
        def scan(self, week):
            return (task,)

        def read(self, slot):
            return ""

        def write_verified(self, slot):
            raise AssertionError("Cancellation must never write")

    @contextmanager
    def factory(cancelled, status):
        try:
            yield Sheet()
        finally:
            closed.append(True)

    dialog = TesthusetDialog(service, factory, IsoWeek(2026, 38))
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: bool(dialog._choices))
    dialog.reject()
    qtbot.waitUntil(lambda: not dialog._worker.isRunning())
    assert closed == [True]
