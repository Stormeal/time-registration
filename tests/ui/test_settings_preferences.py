"""Relocated tracking options persist through the real application service."""

from datetime import timedelta

from qi_flow.application.dto import ReminderSettingsView, StartWorkCommand
from qi_flow.ui.settings_page import SettingsPage
from qi_flow.ui.today_page import TodayPage


def build_page(rig, qtbot):
    page = SettingsPage(*rig.window_args)
    qtbot.addWidget(page)
    return page


def test_dsb_branch_selection_persists_scanned_stable_ids_with_no_name_defaults(
    qtbot, rig, tmp_path
):
    from PySide6.QtCore import Qt

    from qi_flow.application.dsb import DsbService
    from qi_flow.domain.testhuset import ProjectTask
    from qi_flow.infrastructure.system import UuidIdentifierGenerator
    from qi_flow.infrastructure.testhuset_cache import JsonTaskCache

    tasks = (
        ProjectTask("11-22", "Team Web, DSB", "Teknisk Tester"),
        ProjectTask("33-44", "Internal", "Planning"),
    )
    cache = JsonTaskCache(tmp_path / "branches.json")
    cache.replace(tasks)
    dsb = DsbService(rig.uow, rig.clock, UuidIdentifierGenerator(), cache, testhuset_cache=cache)
    page = SettingsPage(*rig.window_args, dsb=dsb, dsb_sheet_factory=lambda: None)
    qtbot.addWidget(page)
    assert page._dsb_branches.count() == 2
    assert all(page._dsb_branches.item(i).checkState() == Qt.CheckState.Unchecked for i in range(2))
    page._dsb_branches.item(0).setCheckState(Qt.CheckState.Checked)
    page._save_dsb_branches_button.click()
    assert dsb.included_branches() == frozenset({tasks[0].id})
    reopened = SettingsPage(*rig.window_args, dsb=dsb, dsb_sheet_factory=lambda: None)
    qtbot.addWidget(reopened)
    assert reopened._dsb_branches.item(0).checkState() == Qt.CheckState.Checked
    assert reopened._dsb_branches.item(1).checkState() == Qt.CheckState.Unchecked


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
