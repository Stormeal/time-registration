"""Relocated tracking options persist through the real application service."""

from datetime import timedelta

from qi_flow.application.dto import ReminderSettingsView, StartWorkCommand
from qi_flow.ui.settings_page import SettingsPage
from qi_flow.ui.today_page import TodayPage


def build_page(rig, qtbot):
    page = SettingsPage(*rig.window_args)
    qtbot.addWidget(page)
    return page


def test_settings_saves_both_reminder_thresholds_and_enable_flags(qtbot, rig):
    page = build_page(rig, qtbot)
    assert hasattr(page, "_work_reminder_minutes"), "Relocated reminders need editable thresholds"
    page._work_reminder_minutes.setValue(480)
    page._lunch_reminder_minutes.setValue(35)
    page._lunch_reminder_enabled.setChecked(False)
    page._save_preferences()
    assert rig.service.reminder_settings() == ReminderSettingsView(True, 480, False, 35)
    reopened = build_page(rig, qtbot)
    assert reopened._work_reminder_minutes.value() == 480
    assert reopened._lunch_reminder_minutes.value() == 35
    assert not reopened._lunch_reminder_enabled.isChecked()


def test_settings_preserves_rounding_sleep_target_and_startup(qtbot, rig):
    page = build_page(rig, qtbot)
    page._rounding.setCurrentIndex(page._rounding.findData(15))
    page._sleep_enabled.setChecked(False)
    page._sleep_minutes.setValue(40)
    page._target.setValue(32)
    page._startup_enabled.setChecked(True)
    page._save_preferences()
    saved = rig.service.app_preferences()
    assert (
        saved.rounding_minutes,
        saved.sleep_enabled,
        saved.sleep_threshold_minutes,
        saved.weekly_target_minutes,
    ) == (15, False, 40, 1920)
    assert rig.startup.enabled


def test_reminder_change_takes_effect_without_restarting_today(qtbot, rig):
    rig.service.start_work(StartWorkCommand())
    today = TodayPage(rig.service)
    qtbot.addWidget(today)
    page = build_page(rig, qtbot)
    assert hasattr(page, "_work_reminder_minutes")
    page._work_reminder_minutes.setValue(60)
    page._save_preferences()
    rig.clock.value += timedelta(minutes=61)
    assert rig.service.due_reminders()[0].kind == "work"
    assert rig.service.active_state().session_id is not None


def test_relocated_controls_retain_explanatory_tooltips(qtbot, rig):
    page = build_page(rig, qtbot)
    assert hasattr(page, "_work_reminder_minutes")
    for control in (
        page._rounding,
        page._sleep_minutes,
        page._work_reminder_minutes,
        page._lunch_reminder_minutes,
    ):
        assert control.toolTip()
    assert "never" in page._sleep_enabled.toolTip().lower()
