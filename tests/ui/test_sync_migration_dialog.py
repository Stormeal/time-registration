"""Migration review requires explicit roster/paused-writer consent and verified results."""

import importlib
from datetime import UTC, datetime

from qi_flow.application.sync_migration import MigrationPlan
from qi_flow.application.sync_models import SyncTarget


def dialog(qtbot):
    module = importlib.import_module("qi_flow.ui.sync_migration_dialog")
    view = module.SyncMigrationDialog()
    qtbot.addWidget(view)
    return view


def plan():
    return MigrationPlan(
        SyncTarget("sheet", "log"),
        ("office", "home"),
        "fingerprint",
        (),
        datetime(2026, 10, 3, 12, tzinfo=UTC),
    )


def test_review_requires_explicit_pause_and_unique_roster(qtbot):
    view = dialog(qtbot)
    view.roster_edit.setPlainText("office\nhome")
    assert not view.review_button.isEnabled()
    view.paused_check.setChecked(True)
    assert view.review_button.isEnabled()
    with qtbot.waitSignal(view.migration_requested) as emitted:
        view.review_button.click()
    request = emitted.args[0]
    assert request.action == "begin"
    assert request.participants == ("office", "home")
    assert request.writers_paused
    assert not view.review_button.isEnabled()
    view.show_failure("Could not verify safety copy")
    assert view.review_button.isEnabled()
    view.roster_edit.setPlainText("office\noffice")
    assert not view.review_button.isEnabled()


def test_only_verified_all_participant_status_enables_cutover(qtbot):
    view = dialog(qtbot)
    view.roster_edit.setPlainText("office\nhome")
    view.participant_edit.setText("office")
    view.paused_check.setChecked(True)
    view.show_status(plan(), ("office",), completed=False)
    assert view.contribute_button.isEnabled()
    assert not view.complete_button.isEnabled()
    view.show_status(plan(), ("office", "home"), completed=False)
    assert view.complete_button.isEnabled()
    with qtbot.waitSignal(view.migration_requested) as emitted:
        view.complete_button.click()
    assert emitted.args[0].participant == "office"
    assert emitted.args[0].action == "complete"
    view.show_failure("V1 writes resumed")
    assert "V1" in view.status_label.text()
    view.paused_check.setChecked(False)
    assert not view.complete_button.isEnabled()


def test_changed_roster_invalidates_verified_review_and_cancel_requests_worker_stop(qtbot):
    view = dialog(qtbot)
    view.paused_check.setChecked(True)
    view.participant_edit.setText("office")
    view.show_status(plan(), ("office", "home"), completed=False)
    view.roster_edit.setPlainText("office\nnew machine")
    assert not view.complete_button.isEnabled()
    view.set_busy(True)
    with qtbot.waitSignal(view.cancel_requested):
        view.reject()
