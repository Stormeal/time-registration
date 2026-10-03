"""Explicit dates, DST occurrences and unsaved correction interactions."""

from datetime import UTC, date, datetime

import pytest
from PySide6.QtCore import QDate, Qt, QTime
from PySide6.QtWidgets import QDialogButtonBox, QMessageBox

from qi_flow.application.dto import ManualWorkSessionCommand
from qi_flow.domain.models import DeductionKind, IsoWeek
from qi_flow.ui.manual_entry_dialog import ManualEntryDialog
from qi_flow.ui.session_editor_dialog import SessionEditorDialog
from qi_flow.ui.timesheet_page import TimesheetPage


@pytest.fixture(autouse=True)
def message_boxes(monkeypatch):
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Discard)


def overnight(rig):
    rig.clock.value = datetime(2026, 10, 3, 12, tzinfo=UTC)
    return rig.service.add_manual_session(
        ManualWorkSessionCommand(
            datetime(2026, 10, 1, 21, tzinfo=UTC), datetime(2026, 10, 2, 1, tzinfo=UTC)
        )
    )


@pytest.mark.parametrize("selected_date", [date(2026, 10, 1), date(2026, 10, 2)])
def test_either_intersected_day_edits_same_overnight_identity(qtbot, rig, selected_date):
    session = overnight(rig)
    dialog = SessionEditorDialog(rig.service, selected_date)
    qtbot.addWidget(dialog)
    assert dialog._tree.topLevelItemCount() == 1
    assert dialog._selected_value().id == session.id
    assert dialog._start_date.date() == QDate(2026, 10, 1)
    assert dialog._end_date.date() == QDate(2026, 10, 2)
    dialog._start.setTime(QTime(22, 30))
    dialog._end.setTime(QTime(3, 30))
    dialog._save.click()
    saved = rig.service.completed_sessions()[0]
    assert saved.id == session.id
    assert saved.actual_started_at == datetime(2026, 10, 1, 20, 30, tzinfo=UTC)
    assert saved.actual_ended_at == datetime(2026, 10, 2, 1, 30, tzinfo=UTC)
    assert rig.service.entry_history_for_day(selected_date)[0].entity_id == str(session.id)


def test_manual_overnight_lunch_uses_independent_dates_and_recalculates_totals(qtbot, rig):
    session = overnight(rig)
    dialog = ManualEntryDialog(rig.service, date(2026, 10, 2), DeductionKind.LUNCH, session.id)
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog._start_date.isVisible() and dialog._end_date.isVisible()
    dialog._start_date.setDate(QDate(2026, 10, 1))
    dialog._start.setTime(QTime(23, 45))
    dialog._end_date.setDate(QDate(2026, 10, 2))
    dialog._end.setTime(QTime(0, 15))
    dialog._buttons.button(QDialogButtonBox.StandardButton.Save).click()
    deduction = rig.service.completed_deductions(session.id)[0]
    assert deduction.actual_started_at == datetime(2026, 10, 1, 21, 45, tzinfo=UTC)
    assert deduction.actual_ended_at == datetime(2026, 10, 1, 22, 15, tzinfo=UTC)
    summaries = rig.service.summaries_for_range(date(2026, 10, 1), date(2026, 10, 3))
    assert [day.net_seconds for day in summaries] == [2700, 9900]
    assert rig.service.weekly_progress(IsoWeek(2026, 40)).logged_seconds == 12600


def test_parent_change_preserves_explicitly_entered_endpoints(qtbot, rig):
    session = overnight(rig)
    other = rig.service.add_manual_session(
        ManualWorkSessionCommand(
            datetime(2026, 9, 30, 7, tzinfo=UTC), datetime(2026, 9, 30, 14, tzinfo=UTC)
        )
    )
    dialog = ManualEntryDialog(rig.service, date(2026, 10, 2), DeductionKind.LUNCH)
    qtbot.addWidget(dialog)
    dialog._parent.setCurrentIndex(dialog._parent.findData(str(session.id)))
    dialog._start_date.setDate(QDate(2026, 10, 1))
    dialog._start.setTime(QTime(23, 45))
    dialog._end_date.setDate(QDate(2026, 10, 2))
    dialog._end.setTime(QTime(0, 15))
    dialog._parent.setCurrentIndex(dialog._parent.findData(str(other.id)))
    assert dialog._start_date.date() == QDate(2026, 10, 1)
    assert dialog._end_date.date() == QDate(2026, 10, 2)
    assert dialog._start.time() == QTime(23, 45)
    dialog._buttons.button(QDialogButtonBox.StandardButton.Save).click()
    assert rig.service.completed_deductions(other.id) == []
    assert dialog.result() == 0


@pytest.mark.parametrize("answer", ["Save", "Discard", "Cancel"])
def test_dirty_row_change_has_save_discard_cancel(qtbot, rig, monkeypatch, answer):
    first = overnight(rig)
    second = rig.service.add_manual_session(
        ManualWorkSessionCommand(
            datetime(2026, 10, 1, 7, tzinfo=UTC), datetime(2026, 10, 1, 14, tzinfo=UTC)
        )
    )
    dialog = SessionEditorDialog(rig.service, date(2026, 10, 1))
    qtbot.addWidget(dialog)
    first_item = next(
        dialog._tree.topLevelItem(i)
        for i in range(2)
        if dialog._tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole).id == first.id
    )
    dialog._tree.setCurrentItem(first_item)
    dialog._start.setTime(QTime(22, 30))
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a: getattr(QMessageBox.StandardButton, answer)
    )
    other_item = next(
        dialog._tree.topLevelItem(i)
        for i in range(2)
        if dialog._tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole).id == second.id
    )
    dialog._tree.setCurrentItem(other_item)
    saved = next(s for s in rig.service.completed_sessions() if s.id == first.id)
    assert saved.actual_started_at == datetime(
        2026, 10, 1, 20 if answer == "Save" else 21, 30 if answer == "Save" else 0, tzinfo=UTC
    )
    assert dialog._selected_value().id == (first.id if answer == "Cancel" else second.id)
    if answer == "Cancel":
        assert dialog._start.time() == QTime(22, 30)
        monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Discard)


@pytest.mark.parametrize("action", ["row", "close"])
def test_failed_save_retains_selection_and_draft(qtbot, rig, monkeypatch, action):
    session = overnight(rig)
    rig.service.add_manual_session(
        ManualWorkSessionCommand(
            datetime(2026, 10, 1, 7, tzinfo=UTC), datetime(2026, 10, 1, 14, tzinfo=UTC)
        )
    )
    dialog = SessionEditorDialog(rig.service, date(2026, 10, 1))
    qtbot.addWidget(dialog)
    dialog.show()
    selected = next(
        dialog._tree.topLevelItem(i)
        for i in range(2)
        if dialog._tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole).id == session.id
    )
    dialog._tree.setCurrentItem(selected)
    dialog._start_date.setDate(QDate(2026, 10, 4))
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Save)
    if action == "row":
        dialog._tree.setCurrentItem(
            dialog._tree.topLevelItem(0 if selected is dialog._tree.topLevelItem(1) else 1)
        )
    else:
        dialog.close()
    assert dialog.isVisible()
    assert dialog._selected_value().id == session.id
    assert dialog._start_date.date() == QDate(2026, 10, 4)
    assert next(s for s in rig.service.completed_sessions() if s.id == session.id) == session
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Discard)


@pytest.mark.parametrize("answer", ["Save", "Discard", "Cancel"])
def test_dirty_close_preserves_or_saves_work(qtbot, rig, monkeypatch, answer):
    session = overnight(rig)
    dialog = SessionEditorDialog(rig.service, date(2026, 10, 2))
    qtbot.addWidget(dialog)
    dialog.show()
    dialog._end.setTime(QTime(3, 30))
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a: getattr(QMessageBox.StandardButton, answer)
    )
    dialog.close()
    assert dialog.isVisible() == (answer == "Cancel")
    saved = rig.service.completed_sessions()[0]
    assert saved.id == session.id
    assert saved.actual_ended_at == datetime(
        2026, 10, 2, 1, 30 if answer == "Save" else 0, tzinfo=UTC
    )
    if answer == "Cancel":
        assert dialog._end.time() == QTime(3, 30)
        monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Discard)


@pytest.mark.parametrize("occurrence, expected_hour", [("earlier", 0), ("later", 1)])
def test_repeated_hour_requires_explicit_occurrence(qtbot, rig, occurrence, expected_hour):
    rig.clock.value = datetime(2026, 10, 26, 12, tzinfo=UTC)
    dialog = ManualEntryDialog(rig.service, date(2026, 10, 25))
    qtbot.addWidget(dialog)
    dialog.show()
    dialog._start.setTime(QTime(2, 30))
    dialog._end.setTime(QTime(3, 30))
    dialog._buttons.button(QDialogButtonBox.StandardButton.Save).click()
    assert rig.service.completed_sessions() == []
    assert dialog._start_input.occurrence.isVisible()
    dialog._start_input.occurrence.setCurrentIndex(
        dialog._start_input.occurrence.findData(occurrence)
    )
    dialog._buttons.button(QDialogButtonBox.StandardButton.Save).click()
    assert rig.service.completed_sessions()[0].actual_started_at == datetime(
        2026, 10, 25, expected_hour, 30, tzinfo=UTC
    )


@pytest.mark.parametrize(
    "work_date, start, end",
    [
        (date(2026, 3, 29), QTime(2, 30), QTime(4, 0)),
        (date(2026, 10, 2), QTime(23, 30), QTime(0, 30)),
        (date(2026, 10, 4), QTime(9, 0), QTime(10, 0)),
    ],
)
def test_invalid_or_future_endpoints_do_not_roll_over_or_persist(qtbot, rig, work_date, start, end):
    rig.clock.value = datetime(2026, 10, 3, 12, tzinfo=UTC)
    dialog = ManualEntryDialog(rig.service, work_date)
    qtbot.addWidget(dialog)
    dialog._start.setTime(start)
    dialog._end.setTime(end)
    dialog._buttons.button(QDialogButtonBox.StandardButton.Save).click()
    assert rig.service.completed_sessions() == []
    assert dialog.result() == 0


def test_timesheet_second_day_opens_same_session_and_refreshes_totals(qtbot, rig, monkeypatch):
    session = overnight(rig)
    page = TimesheetPage(rig.service)
    qtbot.addWidget(page)
    page._year, page._month = 2026, 10
    page.refresh()
    item = next(
        group.child(i)
        for row in range(page._tree.topLevelItemCount())
        for group in [page._tree.topLevelItem(row)]
        for i in range(group.childCount())
        if group.child(i).data(0, Qt.ItemDataRole.UserRole).work_date == date(2026, 10, 2)
    )
    page._tree.setCurrentItem(item)

    def edit(dialog):
        assert dialog._selected_value().id == session.id
        dialog._end.setTime(QTime(3, 30))
        dialog._save.click()
        return 1

    monkeypatch.setattr(SessionEditorDialog, "exec", edit)
    page._edit_sessions.click()
    current = page._tree.currentItem().data(0, Qt.ItemDataRole.UserRole)
    assert current.work_date == date(2026, 10, 2)
    assert current.net_seconds == 12600


@pytest.mark.parametrize("dialog_kind", ["manual", "correction"])
def test_persistence_failure_keeps_save_dialog_and_draft(qtbot, rig, monkeypatch, dialog_kind):
    import sqlite3

    session = overnight(rig)
    if dialog_kind == "manual":
        dialog = ManualEntryDialog(rig.service, date(2026, 9, 30))
        dialog._start.setTime(QTime(9, 0))
        dialog._end.setTime(QTime(10, 0))
        command = "add_manual_session"
    else:
        dialog = SessionEditorDialog(rig.service, date(2026, 10, 2))
        dialog._end.setTime(QTime(3, 30))
        command = "update_work_session"
    qtbot.addWidget(dialog)
    dialog.show()

    def unavailable(*args):
        raise sqlite3.OperationalError("synthetic persistence failure")

    monkeypatch.setattr(rig.service, command, unavailable)
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Save)
    before = dialog._end_input.form_value()
    dialog.close()
    assert dialog.isVisible()
    assert dialog._end_input.form_value() == before
    assert rig.service.completed_sessions() == [session]
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Discard)


@pytest.mark.parametrize("occurrence, expected_hour", [("earlier", 0), ("later", 1)])
def test_correction_of_repeated_hour_preserves_saved_occurrence(
    qtbot, rig, occurrence, expected_hour
):
    rig.clock.value = datetime(2026, 10, 26, 12, tzinfo=UTC)
    session = rig.service.add_manual_session(
        ManualWorkSessionCommand(
            datetime(2026, 10, 25, expected_hour, 15, tzinfo=UTC),
            datetime(2026, 10, 25, 3, tzinfo=UTC),
        )
    )
    dialog = SessionEditorDialog(rig.service, date(2026, 10, 25))
    qtbot.addWidget(dialog)
    assert dialog._start_input.occurrence.currentData() == occurrence
    dialog._start.setTime(QTime(2, 30))
    assert dialog._start_input.occurrence.currentData() is None
    dialog._save.click()
    assert rig.service.completed_sessions() == [session]
    dialog._start_input.occurrence.setCurrentIndex(
        dialog._start_input.occurrence.findData(occurrence)
    )
    dialog._save.click()
    assert rig.service.completed_sessions()[0].actual_started_at == datetime(
        2026, 10, 25, expected_hour, 30, tzinfo=UTC
    )
