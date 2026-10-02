"""Compact Today keeps tracking, allocated totals and recovery actions distinct."""

from datetime import timedelta

from PySide6.QtWidgets import QInputDialog, QMessageBox

from qi_flow.application.dto import StartWorkCommand, UpdateDayDetailsCommand
from qi_flow.domain.models import WorkLocation
from qi_flow.domain.testhuset import ProjectTask
from qi_flow.ui.main_window import MainWindow
from qi_flow.ui.today_page import TodayPage


def make_page(qtbot, rig):
    page = TodayPage(rig.service)
    qtbot.addWidget(page)
    page._refresh_timer.stop()
    page.show()
    return page


def test_today_actions_share_timer_row_in_wide_window(qtbot, rig, qapp):
    page = make_page(qtbot, rig)
    page.resize(760, 620)
    qapp.processEvents()

    assert page._start_work.geometry().left() > page._timer.geometry().right()
    assert page._start_work.geometry().top() < page._session_detail.geometry().bottom()


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


def test_start_prompts_with_default_task_and_assigns_choice_to_new_session(qtbot, rig, monkeypatch):
    class Testhuset:
        def __init__(self):
            self.assigned = []

        def tasks(self):
            return (
                ProjectTask("11-22", "Project", "Default"),
                ProjectTask("33-44", "Other", "Task"),
            )

        def default_task_id(self):
            return "11-22"

        def assign_active(self, session_id, task_id):
            self.assigned.append((session_id, task_id))

    assignment = Testhuset()
    prompt = []

    def choose_item(_parent, title, label, items, current, editable):
        prompt.append((title, label, items, current, editable))
        return items[1], True

    monkeypatch.setattr(QInputDialog, "getItem", choose_item)
    page = make_page(qtbot, rig)
    page._testhuset = assignment

    page._start_work.click()

    assert prompt == [
        (
            "Choose EazyProject task",
            "Project / task for this work session:",
            ["Project / Default", "Other / Task"],
            0,
            False,
        )
    ]
    state = rig.service.active_state()
    assert state.session_id is not None
    assert assignment.assigned == [(state.session_id, "33-44")]
    assert assignment.default_task_id() == "11-22"


def test_canceling_task_prompt_does_not_start_work(qtbot, rig, monkeypatch):
    class Testhuset:
        def tasks(self):
            return (ProjectTask("11-22", "Project", "Default"),)

        def default_task_id(self):
            return "11-22"

    monkeypatch.setattr(QInputDialog, "getItem", lambda *args: ("", False))
    page = make_page(qtbot, rig)
    page._testhuset = Testhuset()

    page._start_work.click()

    assert rig.service.active_state().session_id is None


def test_start_without_scanned_tasks_keeps_existing_start_behavior(qtbot, rig, monkeypatch):
    class Testhuset:
        def tasks(self):
            return ()

    monkeypatch.setattr(
        QInputDialog,
        "getItem",
        lambda *args: (_ for _ in ()).throw(AssertionError("No task choices to show")),
    )
    page = make_page(qtbot, rig)
    page._testhuset = Testhuset()

    page._start_work.click()

    assert rig.service.active_state().session_id is not None


def test_failed_task_assignment_rolls_back_new_active_session(qtbot, rig, monkeypatch):
    class Testhuset:
        def tasks(self):
            return (ProjectTask("11-22", "Project", "Default"),)

        def default_task_id(self):
            return "11-22"

        def assign_active(self, _session_id, _task_id):
            raise ValueError("Task list changed. Scan EazyProject again.")

    monkeypatch.setattr(QInputDialog, "getItem", lambda *args: ("Project / Default", True))
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: None)
    page = make_page(qtbot, rig)
    page._testhuset = Testhuset()

    page._start_work.click()

    assert rig.service.active_state().session_id is None


def test_today_actions_use_side_column_when_wide_and_row_when_narrow(qtbot, rig, qapp):
    page = make_page(qtbot, rig)
    page._start_work.click()
    page.refresh()
    buttons = (page._lunch, page._finish_work, page._start_at)

    page.resize(760, 520)
    qapp.processEvents()
    assert page.width() == 760
    assert len({button.geometry().center().x() for button in buttons}) == 1
    assert [button.geometry().y() for button in buttons] == sorted(
        button.geometry().y() for button in buttons
    )
    assert all(button.geometry().left() > page._timer.geometry().right() for button in buttons)

    page.resize(600, 520)
    qapp.processEvents()
    assert page.width() < 720
    assert len({button.geometry().center().y() for button in buttons}) == 1
    assert [button.geometry().x() for button in buttons] == sorted(
        button.geometry().x() for button in buttons
    )


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
