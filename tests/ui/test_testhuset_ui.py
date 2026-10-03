"""UI interaction tests for assignments and the explicit fill decision boundary."""

from contextlib import contextmanager
from datetime import UTC, date, datetime

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt, QTime
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QDialogButtonBox, QMessageBox, QScrollArea

from qi_flow.application.dsb import DsbService
from qi_flow.application.dto import ManualWorkSessionCommand, UpdateDayDetailsCommand
from qi_flow.application.testhuset import TesthusetService
from qi_flow.application.time_tracking import TimeTrackingApplicationService
from qi_flow.domain.models import DeductionKind, IsoWeek, WorkLocation
from qi_flow.domain.testhuset import ProjectTask
from qi_flow.infrastructure.sqlite.database import SQLiteDatabase
from qi_flow.infrastructure.sqlite.repositories import SQLiteUnitOfWork
from qi_flow.infrastructure.system import UuidIdentifierGenerator
from qi_flow.infrastructure.testhuset_cache import JsonTaskCache
from qi_flow.ui.history_dialog import HistoryDialog
from qi_flow.ui.manual_entry_dialog import ManualEntryDialog
from qi_flow.ui.session_editor_dialog import SessionEditorDialog
from qi_flow.ui.testhuset_dialog import TesthusetDialog
from qi_flow.ui.timesheet_page import TimesheetPage


class Clock:
    def now(self):
        return datetime(2026, 9, 18, tzinfo=UTC)


def build(tmp_path, service_type=TesthusetService):
    database = SQLiteDatabase(tmp_path / "time.sqlite3")
    database.initialize()
    cache = JsonTaskCache(tmp_path / "testhuset-projects.json")
    task = ProjectTask("11-22", "Example", "Testing")
    cache.replace((task,))
    ids = UuidIdentifierGenerator()
    service = service_type(lambda: SQLiteUnitOfWork(database), Clock(), ids, cache)
    service.set_default(task.id)
    tracking = TimeTrackingApplicationService(lambda: SQLiteUnitOfWork(database), Clock(), ids)
    session = tracking.add_manual_session(
        ManualWorkSessionCommand(
            datetime(2026, 9, 14, 7, tzinfo=UTC), datetime(2026, 9, 14, 14, 45, tzinfo=UTC)
        )
    )
    return service, tracking, session, task


def test_hidden_seconds_are_not_saved_from_minute_only_editors(qtbot, tmp_path) -> None:
    _, tracking, _, _ = build(tmp_path)
    manual = ManualEntryDialog(tracking, date(2026, 9, 16))
    qtbot.addWidget(manual)
    manual._start.setTime(QTime(15, 0, 45))
    manual._end.setTime(QTime(17, 0, 30))
    manual._buttons.button(QDialogButtonBox.StandardButton.Save).click()
    saved = tracking.completed_sessions_for_day(date(2026, 9, 16))[0]
    assert saved.actual_started_at == datetime(2026, 9, 16, 13, tzinfo=UTC)
    assert saved.actual_ended_at == datetime(2026, 9, 16, 15, tzinfo=UTC)
    correction = SessionEditorDialog(tracking, date(2026, 9, 16))
    qtbot.addWidget(correction)
    correction._start.setTime(QTime(14, 0, 45))
    correction._end.setTime(QTime(18, 0, 30))
    correction._save.click()
    saved = tracking.completed_sessions_for_day(date(2026, 9, 16))[0]
    assert saved.actual_started_at == datetime(2026, 9, 16, 12, tzinfo=UTC)
    assert saved.actual_ended_at == datetime(2026, 9, 16, 16, tzinfo=UTC)


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


def test_wheel_over_task_assignment_scrolls_editor_without_changing_assignment(
    qtbot, tmp_path, qapp
) -> None:
    service, tracking, _session, _task = build(tmp_path)
    dialog = SessionEditorDialog(tracking, date(2026, 9, 14), service)
    qtbot.addWidget(dialog)
    dialog.resize(840, 320)
    dialog.show()
    dialog._tree.setCurrentItem(dialog._tree.topLevelItem(0))
    dialog._task.setCurrentIndex(0)
    dialog._task.setFocus()
    before = dialog._task.currentIndex()
    scroll = dialog.findChild(QScrollArea)
    assert scroll is not None
    assert scroll.verticalScrollBar().maximum() > 0
    local = QPoint(dialog._task.width() // 2, dialog._task.height() // 2)
    event = QWheelEvent(
        QPointF(local),
        QPointF(dialog._task.mapToGlobal(local)),
        QPoint(),
        QPoint(0, -120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )

    qapp.sendEvent(dialog._task, event)

    assert dialog._task.currentIndex() == before
    assert scroll.verticalScrollBar().value() > 0


def test_selected_completed_session_offers_a_preselected_lunch_dialog(
    qtbot, tmp_path, monkeypatch
) -> None:
    service, tracking, session, _ = build(tmp_path)
    dialog = SessionEditorDialog(tracking, date(2026, 9, 14), service)
    qtbot.addWidget(dialog)
    dialog._tree.setCurrentItem(dialog._tree.topLevelItem(0))
    assert dialog._add_lunch.isEnabled()

    captured: dict[str, object] = {}

    class LunchDialog:
        def __init__(self, *args, **kwargs) -> None:
            captured.update(kwargs)

        def exec(self) -> None:
            return None

    monkeypatch.setattr("qi_flow.ui.session_editor_dialog.ManualEntryDialog", LunchDialog)
    qtbot.mouseClick(dialog._add_lunch, Qt.MouseButton.LeftButton)

    assert captured == {
        "deduction_kind": DeductionKind.LUNCH,
        "parent_session_id": session.id,
    }


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
    assert page._testhuset_button.isEnabled()
    assert "2026" in page._week_summary.text()


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


def test_manual_entry_exposes_both_endpoint_dates_and_exact_minutes(qtbot, tmp_path) -> None:
    _, tracking, _, _ = build(tmp_path)
    dialog = ManualEntryDialog(tracking, date(2026, 9, 14))
    qtbot.addWidget(dialog)

    assert dialog._date.date().toPython() == date(2026, 9, 14)
    assert dialog._end_date.date().toPython() == date(2026, 9, 14)
    assert dialog._start.displayFormat() == "HH:mm"
    assert dialog._end.displayFormat() == "HH:mm"


def test_manual_break_label_still_creates_break_deduction(qtbot, tmp_path) -> None:
    _, tracking, session, _ = build(tmp_path)
    dialog = ManualEntryDialog(tracking, date(2026, 9, 14))
    qtbot.addWidget(dialog)

    break_index = dialog._kind.findText("Break")
    assert break_index >= 0
    dialog._kind.setCurrentIndex(break_index)
    dialog._start.setTime(QTime(12, 0))
    dialog._end.setTime(QTime(12, 15))
    save = dialog._buttons.button(QDialogButtonBox.StandardButton.Save)
    assert save is not None
    qtbot.mouseClick(save, Qt.MouseButton.LeftButton)

    deductions = tracking.completed_deductions(session.id)
    assert len(deductions) == 1
    assert deductions[0].kind is DeductionKind.SLEEP_BREAK


def test_session_editor_uses_saved_endpoint_dates_and_exact_minutes(qtbot, tmp_path) -> None:
    service, tracking, _, _ = build(tmp_path)
    dialog = SessionEditorDialog(tracking, date(2026, 9, 14), service)
    qtbot.addWidget(dialog)
    session_item = dialog._tree.topLevelItem(0)
    dialog._tree.setCurrentItem(session_item)

    assert dialog._start_date.date().toPython() == date(2026, 9, 14)
    assert dialog._end_date.date().toPython() == date(2026, 9, 14)
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
    dialog._note.setPlainText("DSB office day\nKøbenhavn")
    qtbot.mouseClick(dialog._save_office, Qt.MouseButton.LeftButton)

    details = tracking.day_details(date(2026, 9, 14))
    assert details is not None
    assert details.location is WorkLocation.OFFICE
    assert details.note == "DSB office day\nKøbenhavn"


def test_manual_entry_cancel_confirms_before_discarding_changes(
    qtbot, tmp_path, monkeypatch
) -> None:
    _, tracking, _, _ = build(tmp_path)
    dialog = ManualEntryDialog(tracking, date(2026, 9, 14))
    qtbot.addWidget(dialog)
    dialog.show()
    dialog._start.setTime(QTime(8, 30))

    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *args: QMessageBox.StandardButton.Cancel),
    )
    dialog.close()
    assert dialog.isVisible()

    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *args: QMessageBox.StandardButton.Discard),
    )
    dialog.close()
    assert not dialog.isVisible()


def test_deleted_entry_is_recoverable_from_history_dialog(qtbot, tmp_path, monkeypatch) -> None:
    _, tracking, session, _ = build(tmp_path)
    tracking.delete_work_session(session.id)
    dialog = HistoryDialog(tracking, date(2026, 9, 14))
    qtbot.addWidget(dialog)
    assert dialog._tree.topLevelItemCount() == 1
    dialog._tree.setCurrentItem(dialog._tree.topLevelItem(0))
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *args: QMessageBox.StandardButton.Yes),
    )

    dialog._restore_selected()

    assert tracking.completed_sessions_for_day(date(2026, 9, 14))[0].id == session.id


def test_disabled_history_restore_explains_required_selection(qtbot, tmp_path) -> None:
    _, tracking, session, _ = build(tmp_path)
    tracking.delete_work_session(session.id)
    dialog = HistoryDialog(tracking, date(2026, 9, 14))
    qtbot.addWidget(dialog)

    assert not dialog._restore.isEnabled()
    assert "select" in dialog._restore.toolTip().lower()


def test_delete_requires_confirmation(qtbot, tmp_path, monkeypatch) -> None:
    _, tracking, _session, _ = build(tmp_path)
    dialog = SessionEditorDialog(tracking, date(2026, 9, 14))
    qtbot.addWidget(dialog)
    dialog._tree.setCurrentItem(dialog._tree.topLevelItem(0))

    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *args: QMessageBox.StandardButton.Cancel),
    )
    dialog._delete_selected()
    assert len(tracking.completed_sessions_for_day(date(2026, 9, 14))) == 1

    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *args: QMessageBox.StandardButton.Yes),
    )
    dialog._delete_selected()
    assert tracking.completed_sessions_for_day(date(2026, 9, 14)) == []
    assert tracking.entry_history_for_day(date(2026, 9, 14))


def test_correction_close_confirms_unsaved_interval(qtbot, tmp_path, monkeypatch) -> None:
    _, tracking, _, _ = build(tmp_path)
    dialog = SessionEditorDialog(tracking, date(2026, 9, 14))
    qtbot.addWidget(dialog)
    dialog.show()
    dialog._tree.setCurrentItem(dialog._tree.topLevelItem(0))
    dialog._start.setTime(QTime(8, 0))

    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *args: QMessageBox.StandardButton.Cancel),
    )
    dialog.close()
    assert dialog.isVisible()

    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *args: QMessageBox.StandardButton.Discard),
    )
    dialog.close()
    assert not dialog.isVisible()


@pytest.mark.parametrize("service_type", [TesthusetService, DsbService])
def test_no_writes_before_fill_confirmation(qtbot, tmp_path, service_type) -> None:
    service, _, _, task = build(tmp_path, service_type)
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

        def commit_verified(self):
            pass

    @contextmanager
    def factory(cancelled, status):
        try:
            yield Sheet()
        finally:
            closed.append(True)

    dialog = TesthusetDialog(service, factory, IsoWeek(2026, 38))
    qtbot.addWidget(dialog)
    assert dialog._fill.toolTip() == "The weekly review is loading."
    qtbot.waitUntil(lambda: bool(dialog._choices))
    assert writes == []
    assert not dialog._fill.isEnabled()
    assert dialog._choices[0].currentData() is None
    dialog._choices[0].setCurrentIndex(1)
    assert dialog._fill.isEnabled()
    assert writes == []
    qtbot.mouseClick(dialog._fill, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not dialog._worker.isRunning())
    assert len(writes) == 1
    assert closed == [True]


@pytest.mark.parametrize("service_type", [TesthusetService, DsbService])
def test_preview_allows_keeping_a_differing_external_value(qtbot, tmp_path, service_type) -> None:
    service, _, _, task = build(tmp_path, service_type)
    writes = []

    class Sheet:
        def scan(self, week):
            return (task,)

        def read(self, slot):
            return "1,00"

        def write_verified(self, slot):
            writes.append(slot)

        def commit_verified(self):
            pass

    @contextmanager
    def factory(cancelled, status):
        yield Sheet()

    dialog = TesthusetDialog(service, factory, IsoWeek(2026, 38))
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: bool(dialog._choices))
    dialog._choices[0].setCurrentIndex(2)
    qtbot.mouseClick(dialog._fill, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not dialog._worker.isRunning())
    assert writes == []


@pytest.mark.parametrize("service_type", [TesthusetService, DsbService])
def test_cancel_preview_closes_temporary_session_without_writes(
    qtbot, tmp_path, service_type
) -> None:
    service, _, _, task = build(tmp_path, service_type)
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


@pytest.mark.parametrize("service_type", [TesthusetService, DsbService])
def test_every_differing_row_requires_a_choice_and_unselection_disables_fill(
    qtbot, tmp_path, service_type
) -> None:
    service, tracking, _, task = build(tmp_path, service_type)
    for day in (15, 16, 17):
        tracking.add_manual_session(
            ManualWorkSessionCommand(
                datetime(2026, 9, day, 7, tzinfo=UTC),
                datetime(2026, 9, day, 14, 45, tzinfo=UTC),
            )
        )
    writes = []
    values = {14: "", 15: "0.00", 16: "2,00", 17: "7,75"}

    class Sheet:
        def scan(self, week):
            return (task,)

        def read(self, slot):
            return values[slot.work_date.day]

        def write_verified(self, slot):
            writes.append(slot)

    @contextmanager
    def factory(cancelled, status):
        yield Sheet()

    dialog = TesthusetDialog(service, factory, IsoWeek(2026, 38))
    qtbot.addWidget(dialog)
    try:
        qtbot.waitUntil(lambda: dialog._table.rowCount() == 4)
        assert set(dialog._choices) == {0, 1, 2}
        assert all(choice.currentData() is None for choice in dialog._choices.values())
        assert not dialog._fill.isEnabled()
        assert dialog._table.cellWidget(3, 4) is None
        for row in (0, 1):
            dialog._choices[row].setCurrentIndex(1)
            assert not dialog._fill.isEnabled()
        dialog._choices[2].setCurrentIndex(2)
        assert dialog._fill.isEnabled()
        dialog._choices[1].setCurrentIndex(0)
        assert not dialog._fill.isEnabled()
        qtbot.mouseClick(dialog._fill, Qt.MouseButton.LeftButton)
        assert not dialog._worker.confirmed
        assert writes == []
        dialog._choices[1].setCurrentIndex(2)
        assert dialog._fill.isEnabled()
    finally:
        dialog.reject()
        qtbot.waitUntil(lambda: not dialog._worker.isRunning())


@pytest.mark.parametrize("service_type", [TesthusetService, DsbService])
def test_matching_rows_need_no_decision_and_are_never_written(qtbot, tmp_path, service_type):
    service, _, _, task = build(tmp_path, service_type)

    class Sheet:
        def scan(self, week):
            return (task,)

        def read(self, slot):
            return "7,75"

        def write_verified(self, slot):
            raise AssertionError("Matching rows must not be written")

    @contextmanager
    def factory(cancelled, status):
        yield Sheet()

    dialog = TesthusetDialog(service, factory, IsoWeek(2026, 38))
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: dialog._table.rowCount() == 1)
    assert dialog._choices == {}
    assert dialog._fill.isEnabled()
    qtbot.mouseClick(dialog._fill, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not dialog._worker.isRunning())


@pytest.mark.parametrize("service_type", [TesthusetService, DsbService])
@pytest.mark.parametrize("error_type", [ValueError, RuntimeError])
def test_fill_failure_closes_browser_and_requires_a_new_review(
    qtbot, tmp_path, service_type, error_type
):
    service, _, _, task = build(tmp_path, service_type)
    attempts = []
    closed = []

    class Sheet:
        def scan(self, week):
            return (task,)

        def read(self, slot):
            return ""

        def write_verified(self, slot):
            attempts.append(slot)
            raise error_type("Uncertain save")

        def commit_verified(self):
            raise AssertionError("Failed batch must never be sent")

    @contextmanager
    def factory(cancelled, status):
        try:
            yield Sheet()
        finally:
            closed.append(True)

    dialog = TesthusetDialog(service, factory, IsoWeek(2026, 38))
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: bool(dialog._choices))
    dialog._choices[0].setCurrentIndex(1)
    qtbot.mouseClick(dialog._fill, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not dialog._worker.isRunning())
    qtbot.waitUntil(lambda: "new preview" in dialog._status.text().lower())
    assert len(attempts) == 1
    assert closed == [True]
    assert not dialog._fill.isEnabled()
    assert not dialog._table.isEnabled()
    qtbot.mouseClick(dialog._fill, Qt.MouseButton.LeftButton)
    assert len(attempts) == 1
