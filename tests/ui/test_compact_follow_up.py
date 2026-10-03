"""User-reported compact UI regressions and follow-up interactions."""

from datetime import timedelta

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QComboBox,
    QDialogButtonBox,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
)

from qi_flow.application.dto import (
    FinishDeductionCommand,
    FinishWorkCommand,
    StartDeductionCommand,
    StartWorkCommand,
)
from qi_flow.domain.models import DeductionKind
from qi_flow.domain.testhuset import ProjectTask
from qi_flow.ui.daily_note_dialog import DailyNoteDialog
from qi_flow.ui.main_window import MainWindow
from qi_flow.ui.session_editor_dialog import SessionEditorDialog
from qi_flow.ui.timesheet_page import TimesheetPage
from qi_flow.ui.today_page import TodayPage


def test_launch_uses_tall_compact_size(qtbot, rig):
    window = MainWindow(*rig.window_args)
    qtbot.addWidget(window)
    assert window.size().width() == 760
    assert window.size().height() == 860


def test_session_editor_keeps_table_readable_and_selects_session(qtbot, rig):
    rig.service.start_work(StartWorkCommand(rig.clock.value - timedelta(hours=2)))
    rig.service.finish_work(FinishWorkCommand())
    dialog = SessionEditorDialog(rig.service, rig.service.today_summary().work_date)
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog._tree.height() >= 180
    assert dialog._tree.currentItem() is not None
    assert dialog._save.isEnabled()


def test_session_editor_opens_at_wide_two_column_size(qtbot, rig):
    dialog = SessionEditorDialog(rig.service, rig.service.today_summary().work_date)
    qtbot.addWidget(dialog)

    assert dialog.width() == 1100


def test_session_editor_gives_correction_controls_room_without_horizontal_scroll(qtbot, rig, qapp):
    dialog = SessionEditorDialog(rig.service, rig.service.today_summary().work_date)
    qtbot.addWidget(dialog)
    dialog.show()
    qapp.processEvents()

    assert dialog._tree.width() <= 320
    assert dialog._controls_scroll.horizontalScrollBar().maximum() == 0


def test_session_editor_long_task_name_does_not_push_controls_offscreen(qtbot, rig, qapp):
    class Tasks:
        def tasks(self):
            return (ProjectTask("task-1", "Very long customer project name " * 5, "Task"),)

    dialog = SessionEditorDialog(rig.service, rig.service.today_summary().work_date, Tasks())
    qtbot.addWidget(dialog)
    dialog.show()
    qapp.processEvents()

    assert dialog._controls_scroll.horizontalScrollBar().maximum() == 0
    assert dialog._save_task.geometry().right() <= dialog._controls_scroll.viewport().width()


def test_disabled_session_editor_actions_explain_required_selection(qtbot, rig):
    dialog = SessionEditorDialog(rig.service, rig.service.today_summary().work_date)
    qtbot.addWidget(dialog)

    assert not dialog._save.isEnabled()
    assert "select" in dialog._save.toolTip().lower()
    assert not dialog._delete.isEnabled()
    assert "select" in dialog._delete.toolTip().lower()
    assert not dialog._add_lunch.isEnabled()
    assert "select a work session" in dialog._add_lunch.toolTip().lower()


def test_active_session_editor_allows_adding_lunch(qtbot, rig, monkeypatch):
    session = rig.service.start_work(StartWorkCommand(rig.clock.value - timedelta(hours=3)))
    dialog = SessionEditorDialog(rig.service, rig.service.today_summary().work_date)
    qtbot.addWidget(dialog)
    selected_ids = []

    def inspect_manual_entry(entry):
        selected_ids.append(entry._parent.currentData())
        return 0

    monkeypatch.setattr(
        "qi_flow.ui.session_editor_dialog.ManualEntryDialog.exec", inspect_manual_entry
    )
    dialog._add_lunch.click()

    assert dialog._add_lunch.isEnabled()
    assert selected_ids == [str(session.session_id)]
    assert rig.service.active_state().session_id == session.session_id


def test_completed_timer_lunch_is_listed_under_active_session(qtbot, rig):
    rig.service.start_work(StartWorkCommand(rig.clock.value - timedelta(hours=3)))
    rig.service.start_deduction(StartDeductionCommand(DeductionKind.LUNCH))
    rig.clock.value += timedelta(minutes=30)
    rig.service.finish_deduction(FinishDeductionCommand())

    dialog = SessionEditorDialog(rig.service, rig.service.today_summary().work_date)
    qtbot.addWidget(dialog)

    running = dialog._tree.topLevelItem(0)
    assert running is not None and running.childCount() == 1
    assert running.child(0).text(0) == "Lunch"


def test_month_is_between_previous_and_next_at_compact_width(qtbot, rig):
    window = MainWindow(*rig.window_args)
    qtbot.addWidget(window)
    window.show_timesheet()
    page = window.findChild(TimesheetPage)
    previous = next(b for b in page.findChildren(QPushButton) if b.text() == "Previous month")
    assert page._month_label.geometry().center().y() == previous.geometry().center().y()
    assert page._month_label.x() > previous.geometry().right()


def test_settings_wheel_never_changes_focused_fields(qtbot, rig, qapp):
    window = MainWindow(*rig.window_args)
    qtbot.addWidget(window)
    window.show_settings()
    page = window._settings_page
    for field in [*page.findChildren(QAbstractSpinBox), *page.findChildren(QComboBox)]:
        field.setFocus()
        before = field.text() if isinstance(field, QAbstractSpinBox) else field.currentIndex()
        event = QWheelEvent(
            QPointF(10, 10),
            QPointF(10, 10),
            QPoint(),
            QPoint(0, -120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
        qapp.sendEvent(field, event)
        after = field.text() if isinstance(field, QAbstractSpinBox) else field.currentIndex()
        assert after == before


def test_wheel_over_setting_scrolls_page_and_keyboard_still_edits(qtbot, rig, qapp):
    from PySide6.QtWidgets import QScrollArea

    window = MainWindow(*rig.window_args)
    qtbot.addWidget(window)
    window.resize(640, 520)
    window.show_settings()
    scroll = window._pages.currentWidget()
    assert isinstance(scroll, QScrollArea)
    field = window._settings_page._target
    field.setFocus()
    before = field.value()
    event = QWheelEvent(
        QPointF(10, 10),
        QPointF(field.mapToGlobal(QPoint(10, 10))),
        QPoint(),
        QPoint(0, -120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    qapp.sendEvent(field, event)
    assert scroll.verticalScrollBar().value() > 0
    assert field.value() == before
    qtbot.keyClick(field, Qt.Key.Key_Up)
    assert field.value() == before + 1


def test_today_note_is_a_dialog_action(qtbot, rig):
    page = TodayPage(rig.service)
    qtbot.addWidget(page)
    page.show()
    assert hasattr(page, "_edit_note")
    assert page._edit_note.isVisible()
    assert not page._note.isVisible()


def test_note_dialog_save_stays_on_original_date_at_midnight(qtbot, rig):
    page = TodayPage(rig.service)
    qtbot.addWidget(page)
    page.show()
    page._refresh_timer.stop()
    old_date = page._context_date
    observed_dates = []

    def edit():
        dialog = page.findChild(DailyNoteDialog)
        dialog._note.setPlainText("Yesterday's note")
        rig.clock.value += timedelta(days=1)
        page.refresh()
        observed_dates.append(page._context_date)
        dialog._buttons.button(QDialogButtonBox.StandardButton.Save).click()

    QTimer.singleShot(0, edit)
    page._edit_note.click()
    assert observed_dates == [old_date]
    assert rig.service.day_details(old_date).note == "Yesterday's note"
    assert rig.service.day_details(old_date + timedelta(days=1)) is None


def test_today_start_time_action_opens_real_dialog(qtbot, rig):
    from qi_flow.ui.start_time_dialog import StartTimeDialog

    page = TodayPage(rig.service)
    qtbot.addWidget(page)
    page.show()
    page._refresh_timer.stop()
    assert hasattr(page, "_start_at")

    def enter():
        dialog = page.findChild(StartTimeDialog)
        dialog._start.setDateTime(dialog._start.dateTime().addSecs(-23 * 60))
        dialog._buttons.button(QDialogButtonBox.StandardButton.Save).click()

    QTimer.singleShot(0, enter)
    page._start_at.click()
    assert rig.service.active_state().net_seconds == 23 * 60
    assert page._timer.text() == "00:23:00"
    assert "Change start" in page._start_at.text()


def test_hover_highlights_each_visible_cell_in_same_row(qtbot, rig, monkeypatch):
    page = TimesheetPage(rig.service)
    qtbot.addWidget(page)
    page.resize(640, 860)
    page.show()
    tree = page._tree
    item = tree.topLevelItem(0).child(0)
    hovered = tree.indexFromItem(item).siblingAtColumn(0)
    painted = []
    original = QStyledItemDelegate.paint

    def observe(delegate, painter, option, index):
        if index.siblingAtColumn(0) == hovered:
            painted.append((index.column(), bool(option.state & QStyle.StateFlag.State_MouseOver)))
        original(delegate, painter, option, index)

    monkeypatch.setattr(QStyledItemDelegate, "paint", observe)
    qtbot.mouseMove(tree.viewport(), tree.visualItemRect(item).center())
    qtbot.waitUntil(lambda: tree.hovered_index == hovered)
    painted.clear()
    tree.viewport().repaint()
    assert len({column for column, _ in painted}) >= 2
    assert all(is_hovered for _, is_hovered in painted)


def test_start_dialog_corrects_same_running_session_and_rejects_future(qtbot, rig):
    from qi_flow.ui.start_time_dialog import StartTimeDialog

    rig.service.start_work(StartWorkCommand())
    session_id = rig.service.active_state().session_id
    dialog = StartTimeDialog(rig.service)
    qtbot.addWidget(dialog)
    dialog._start.setDateTime(dialog._start.dateTime().addSecs(-23 * 60))
    dialog._buttons.button(QDialogButtonBox.StandardButton.Save).click()
    assert rig.service.active_state().session_id == session_id
    assert rig.service.active_state().net_seconds == 1380
    invalid = StartTimeDialog(rig.service)
    qtbot.addWidget(invalid)
    invalid._start.setDateTime(invalid._start.dateTime().addSecs(60 * 60))
    invalid._buttons.button(QDialogButtonBox.StandardButton.Save).click()
    assert invalid.result() == 0
    assert invalid._error.text()
    assert rig.service.active_state().net_seconds == 1380


def test_note_cancel_discards_editor_changes_without_changing_saved_note(qtbot, rig, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    page = TodayPage(rig.service)
    qtbot.addWidget(page)
    page.show()
    page._refresh_timer.stop()
    page._note.setPlainText("Saved æøå")
    page._save_context.click()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)

    def cancel():
        dialog = page.findChild(DailyNoteDialog)
        dialog._note.setPlainText("Discard this draft")
        dialog._buttons.button(QDialogButtonBox.StandardButton.Cancel).click()

    QTimer.singleShot(0, cancel)
    page._edit_note.click()
    assert rig.service.day_details(page._context_date).note == "Saved æøå"
    assert page._note.toPlainText() == "Saved æøå"


@pytest.mark.parametrize("blocking", ["sleep", "midnight"])
def test_start_dialog_revalidates_blocking_after_opening(qtbot, rig, blocking):
    from qi_flow.ui.start_time_dialog import StartTimeDialog

    rig.service.start_work(StartWorkCommand(rig.clock.value - timedelta(hours=2)))
    original = rig.service.active_state().actual_started_at
    dialog = StartTimeDialog(rig.service)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog._start.setDateTime(dialog._start.dateTime().addSecs(-23 * 60))
    if blocking == "sleep":
        rig.service.detect_sleep_gap(rig.clock.value - timedelta(hours=1), rig.clock.value)
    else:
        rig.clock.value += timedelta(days=1)
    dialog._buttons.button(QDialogButtonBox.StandardButton.Save).click()
    assert dialog.isVisible()
    assert dialog._error.text()
    assert rig.service.active_state().actual_started_at == original


def test_untouched_editor_with_seconds_closes_without_discard_prompt(qtbot, rig, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    rig.clock.value = rig.clock.value.replace(second=23)
    rig.service.start_work(StartWorkCommand(rig.clock.value - timedelta(hours=2)))
    rig.service.finish_work(FinishWorkCommand())
    dialog = SessionEditorDialog(rig.service, rig.service.today_summary().work_date)
    qtbot.addWidget(dialog)
    prompts = []

    def discard(*args):
        prompts.append(args)
        return QMessageBox.StandardButton.Discard

    monkeypatch.setattr(QMessageBox, "question", discard)
    dialog.show()
    dialog.reject()
    assert prompts == []
    dialog.show()
    dialog._start.setTime(dialog._start.time().addSecs(60))
    dialog.reject()
    assert len(prompts) == 1


def test_manual_deduction_uses_visible_selected_parent_date(qtbot, rig, monkeypatch):
    from datetime import UTC, date, datetime

    from PySide6.QtCore import QDate, QTime
    from PySide6.QtWidgets import QDialogButtonBox

    from qi_flow.application.dto import ManualWorkSessionCommand
    from qi_flow.ui.manual_entry_dialog import ManualEntryDialog

    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Discard)
    rig.clock.value = datetime(2026, 10, 3, 12, tzinfo=UTC)
    session = rig.service.add_manual_session(
        ManualWorkSessionCommand(
            datetime(2026, 10, 1, 7, tzinfo=UTC), datetime(2026, 10, 1, 14, tzinfo=UTC)
        )
    )
    dialog = ManualEntryDialog(rig.service, date(2026, 10, 2))
    qtbot.addWidget(dialog)
    dialog.show()
    dialog._kind.setCurrentIndex(dialog._kind.findData(DeductionKind.LUNCH.value))
    dialog._parent.setCurrentIndex(dialog._parent.findData(str(session.id)))
    dialog._start.setTime(QTime(12, 0))
    dialog._end.setTime(QTime(12, 30))

    assert dialog._date.isVisible()
    assert dialog._date.date() == QDate(2026, 10, 1)
    dialog._buttons.button(QDialogButtonBox.StandardButton.Save).click()
    deduction = rig.service.completed_deductions(session.id)[0]
    assert deduction.actual_started_at == datetime(2026, 10, 1, 10, tzinfo=UTC)
    assert deduction.actual_ended_at == datetime(2026, 10, 1, 10, 30, tzinfo=UTC)

