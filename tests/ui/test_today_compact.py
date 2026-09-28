"""Compact Today keeps tracking, allocated totals and recovery actions distinct."""

from datetime import timedelta

from PySide6.QtWidgets import QMessageBox

from qi_flow.application.dto import StartWorkCommand, UpdateDayDetailsCommand
from qi_flow.domain.models import WorkLocation
from qi_flow.ui.main_window import MainWindow
from qi_flow.ui.today_page import TodayPage


def make_page(qtbot, rig):
    page = TodayPage(rig.service)
    qtbot.addWidget(page)
    page._refresh_timer.stop()
    page.show()
    return page


def test_compact_today_work_lunch_finish_and_undo_flow(qtbot, rig):
    page = make_page(qtbot, rig)
    assert hasattr(page, "_lunch_duration"), "Lunch duration must remain visible"
    page._start_work.click()
    rig.clock.value += timedelta(hours=2)
    page._lunch.click()
    rig.clock.value += timedelta(minutes=30)
    page.refresh()
    assert not page._finish_work.isEnabled()
    assert "00:30:00" in page._lunch_duration.text()
    assert "End lunch" in page._lunch_duration.text()
    page._lunch.click()
    assert rig.service.active_state().active_deduction_kind is None
    page._undo.click()
    assert rig.service.active_state().active_deduction_kind is not None
    page._lunch.click()
    rig.clock.value += timedelta(minutes=30)
    page._finish_work.click()
    assert rig.service.active_state().session_id is None
    assert page._day_total.text() == "02:30"


def test_today_total_survives_finish_and_second_session(qtbot, rig):
    page = make_page(qtbot, rig)
    assert hasattr(page, "_day_total")
    page._start_work.click()
    rig.clock.value += timedelta(hours=1)
    page._finish_work.click()
    rig.clock.value += timedelta(minutes=30)
    page._start_work.click()
    rig.clock.value += timedelta(hours=1)
    page.refresh()
    assert page._day_total.text() == "02:00"
    assert page._timer.text() == "01:00:00"
    assert "in progress" in page._sessions.text()


def test_editor_refreshes_saved_context_without_overwriting_draft(qtbot, rig, monkeypatch):
    page = make_page(qtbot, rig)
    assignment_service = object()
    page._testhuset = assignment_service
    received = []

    class Editor:
        def __init__(self, service, work_date, testhuset):
            received.append(testhuset)
            self.work_date = work_date

        def exec(self):
            rig.service.update_day_details(
                UpdateDayDetailsCommand(self.work_date, WorkLocation.OFFICE, "Edited context")
            )

    monkeypatch.setattr("qi_flow.ui.today_page.SessionEditorDialog", Editor)
    page._edit_sessions.click()
    assert received == [assignment_service]
    assert page._note.toPlainText() == "Edited context"
    page._note.setPlainText("Draft")
    page._edit_sessions.click()
    assert page._note.toPlainText() == "Draft"


def test_today_context_save_preserves_unicode_note(qtbot, rig):
    page = make_page(qtbot, rig)
    page._note.setPlainText("Prøve\næøå")
    page._office.setChecked(True)
    page._save_context.click()
    assert rig.service.day_details(rig.clock.value.date()).note == "Prøve\næøå"


def test_return_to_today_refreshes_saved_context_and_preserves_draft(qtbot, rig):
    window = MainWindow(*rig.window_args)
    qtbot.addWidget(window)
    window.show()
    page = window.findChild(TodayPage)
    page._refresh_timer.stop()
    window.show_timesheet()
    rig.service.update_day_details(
        UpdateDayDetailsCommand(page._context_date, WorkLocation.OFFICE, "Edited from Timesheet")
    )
    window._navigation.setCurrentIndex(0)
    assert page._note.toPlainText() == "Edited from Timesheet"
    page._note.setPlainText("My draft")
    window.show_timesheet()
    window._navigation.setCurrentIndex(0)
    assert page._note.toPlainText() == "My draft"


def test_unsaved_note_survives_timer_refresh_and_midnight(qtbot, rig, monkeypatch):
    page = make_page(qtbot, rig)
    page._note.setPlainText("Unsaved yesterday")
    assert hasattr(page, "_context_date")
    old_date = page._context_date
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Cancel)
    rig.clock.value += timedelta(days=1)
    page.refresh()
    assert page._note.toPlainText() == "Unsaved yesterday"
    assert page._context_date == old_date
    page._save_context.click()
    assert rig.service.day_details(old_date).note == "Unsaved yesterday"
    assert page._context_date != old_date


def test_pending_recovery_disables_actions_and_can_be_reopened(qtbot, rig, monkeypatch):
    rig.service.start_work(StartWorkCommand(rig.clock.value - timedelta(hours=2)))
    rig.service.detect_sleep_gap(rig.clock.value - timedelta(hours=1), rig.clock.value)
    monkeypatch.setattr(TodayPage, "_show_sleep_resolution_if_needed", lambda self: None)
    page = make_page(qtbot, rig)
    assert hasattr(page, "_resolve")
    assert page._resolve.isVisible()
    assert not page._finish_work.isEnabled()

    def include(self):
        rig.service.resolve_sleep_gap("include")

    monkeypatch.setattr(TodayPage, "_show_sleep_resolution_if_needed", include)
    page._resolve.click()
    assert rig.service.pending_sleep_gap() is None
    assert page._finish_work.isEnabled()


def test_startup_recovery_open_timesheet_reaches_connected_shell(qtbot, rig, monkeypatch):
    rig.service.start_work(StartWorkCommand(rig.clock.value - timedelta(days=1)))
    choices = []

    def choose_review(message):
        button = next(button for button in message.buttons() if button.text() == "Open timesheet")
        choices.append(button)
        button.click()
        return 0

    monkeypatch.setattr(QMessageBox, "exec", choose_review)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda self: choices[-1])
    window = MainWindow(*rig.window_args)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window._navigation.currentIndex() == 1)
    assert rig.service.recovery_state() is not None


def test_continued_overnight_work_remains_in_today_session_list(qtbot, rig):
    rig.service.start_work(StartWorkCommand(rig.clock.value - timedelta(days=1)))
    rig.service.continue_recovery()
    page = make_page(qtbot, rig)
    assert "in progress" in page._sessions.text()
    assert page._service.today_summary().session_count == 1
