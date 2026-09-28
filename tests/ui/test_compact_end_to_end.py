"""Compact desktop flow persists corrections and exports after reopening."""

import csv
from datetime import timedelta

from PySide6.QtCore import QTime

from qi_flow.application.time_tracking import TimeTrackingApplicationService
from qi_flow.infrastructure.system import UuidIdentifierGenerator
from qi_flow.ui.main_window import MainWindow
from qi_flow.ui.session_editor_dialog import SessionEditorDialog
from qi_flow.ui.today_page import TodayPage


def test_compact_tracking_correction_restart_and_export(qtbot, rig, tmp_path):
    window = MainWindow(*rig.window_args)
    qtbot.addWidget(window)
    window.show()
    today = window.findChild(TodayPage)
    today._refresh_timer.stop()
    work_date = rig.service.today_summary().work_date
    today._start_work.click()
    rig.clock.value += timedelta(hours=2)
    today._lunch.click()
    rig.clock.value += timedelta(minutes=30)
    today._lunch.click()
    rig.clock.value += timedelta(minutes=30)
    today._finish_work.click()
    rig.clock.value += timedelta(minutes=30)
    today._start_work.click()
    rig.clock.value += timedelta(hours=1)
    today._finish_work.click()
    today._note.setPlainText("Review; æøå")
    today._save_context.click()

    editor = SessionEditorDialog(rig.service, work_date)
    qtbot.addWidget(editor)
    editor._tree.setCurrentItem(editor._tree.topLevelItem(0))
    editor._end.setTime(QTime(15, 30))
    editor._save.click()
    editor.accept()
    window.hide()

    reopened_service = TimeTrackingApplicationService(rig.uow, rig.clock, UuidIdentifierGenerator())
    reopened = MainWindow(reopened_service, *rig.window_args[1:])
    qtbot.addWidget(reopened)
    restored = reopened.findChild(TodayPage)
    restored._refresh_timer.stop()
    assert restored._day_total.text() == "03:45"
    assert restored._note.toPlainText() == "Review; æøå"
    summary = reopened_service.today_summary()
    assert summary.session_count == 2
    assert summary.lunch_seconds == 1800
    assert reopened_service.active_state().session_id is None
    summary_path = tmp_path / "summary.csv"
    detail_path = tmp_path / "detail.csv"
    rig.exporter.write_summary(summary_path, [summary])
    rig.exporter.write_detailed(detail_path, work_date, work_date + timedelta(days=1))
    with summary_path.open(encoding="utf-8", newline="") as source:
        rows = list(csv.DictReader(source, delimiter=";"))
    assert rows[0]["Netto timer"] == "3,75"
    assert rows[0]["Noter"] == "Review; æøå"
    with detail_path.open(encoding="utf-8", newline="") as source:
        details = list(csv.DictReader(source, delimiter=";"))
    assert [row["Type"] for row in details].count("Arbejde") == 2
    assert [row["Type"] for row in details].count("Frokost") == 1
