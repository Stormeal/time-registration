"""Conflict review presents provenance and requires explicit choices for each entry."""

import importlib
from datetime import UTC, datetime

from qi_flow.application.sync_models import (
    SyncChange,
    SyncConflict,
    SyncConflictReview,
    finalize_group,
)

KEY = ("day_details", "2026-10-02")


def reviewed():
    def version(identifier, note, parents=()):
        return finalize_group(
            (
                SyncChange(
                    identifier,
                    2,
                    *KEY,
                    parents,
                    "g-" + identifier,
                    (),
                    "",
                    {},
                    "upsert",
                    {"location": "office", "note": note, "revision": 1},
                    datetime(2026, 10, 3, 12, tzinfo=UTC),
                    "device",
                ),
            )
        )[0]

    base, a, b = (
        version("base", "base note"),
        version("a", "first note", ("base",)),
        version("b", "second note", ("base",)),
    )
    return SyncConflictReview(
        SyncConflict("conflict", (KEY,), (a, b), "concurrent_edit"),
        {KEY: {"location": "office", "note": "local note", "revision": 1}},
        {KEY: ("base",)},
        "local-fingerprint",
        (base,),
    )


def dialog(qtbot):
    module = importlib.import_module("qi_flow.ui.sync_conflict_dialog")
    view = module.SyncConflictDialog(reviewed())
    qtbot.addWidget(view)
    return view


def test_versions_and_causal_base_are_visible_without_preselected_choice(qtbot):
    from PySide6.QtWidgets import QLabel

    view = dialog(qtbot)
    text = "\n".join(label.text() for label in view.findChildren(QLabel))
    for note in ("local note", "base note", "first note", "second note"):
        assert note in text
    assert view.choices[KEY].currentIndex() == 0
    assert not view.save_button.isEnabled()


def test_explicit_local_choice_emits_reviewed_heads_and_owned_payload(qtbot):
    view = dialog(qtbot)
    view.choices[KEY].setCurrentIndex(1)
    with qtbot.waitSignal(view.resolution_requested) as emitted:
        view.save_button.click()
    conflict_id, heads, payloads = emitted.args
    assert conflict_id == "conflict"
    assert heads == frozenset({"a", "b"})
    assert payloads[KEY]["note"] == "local note"
    assert not view.save_button.isEnabled()


def test_explicit_deletion_emits_null_and_cancel_never_emits_a_resolution(qtbot):
    view = dialog(qtbot)
    view.choices[KEY].setCurrentIndex(view.choices[KEY].count() - 1)
    with qtbot.waitSignal(view.resolution_requested) as emitted:
        view.save_button.click()
    assert emitted.args[2] == {KEY: None}
    another = dialog(qtbot)
    signals = []
    another.resolution_requested.connect(lambda *args: signals.append(args))
    another.reject()
    assert signals == []


def test_failed_validation_keeps_review_and_choices_available(qtbot):
    view = dialog(qtbot)
    view.choices[KEY].setCurrentIndex(1)
    view.save_button.click()
    view.show_failure("The selected entries overlap; correct or delete one.")
    assert "overlap" in view.status_label.text()
    assert view.choices[KEY].currentIndex() == 1
    assert view.save_button.isEnabled()


def test_invalid_incoming_date_is_reviewable_and_can_be_deleted(qtbot):
    from dataclasses import replace

    module = importlib.import_module("qi_flow.ui.sync_conflict_dialog")
    original = reviewed()
    key = ("work_session", "bad-work")
    invalid = finalize_group(
        (
            replace(
                original.conflict.changes[0],
                entity_kind=key[0],
                entity_id=key[1],
                payload={"actual_started_at": "bad", "actual_ended_at": "bad"},
            ),
        )
    )[0]
    view = module.SyncConflictDialog(
        SyncConflictReview(
            SyncConflict("invalid", (key,), (invalid,), "invalid_aggregate"),
            {key: None},
            {key: ()},
            "fingerprint",
            (),
        )
    )
    qtbot.addWidget(view)
    view.choices[key].setCurrentIndex(view.choices[key].count() - 1)
    with qtbot.waitSignal(view.resolution_requested) as emitted:
        view.save_button.click()
    assert emitted.args[2] == {key: None}


def test_corrected_explicit_dates_emit_manual_exact_minute_bounds(qtbot):
    from dataclasses import replace

    module = importlib.import_module("qi_flow.ui.sync_conflict_dialog")
    original = reviewed()
    key = ("work_session", "work")
    payload = {
        "actual_started_at": "2026-10-02T07:00:00+00:00",
        "actual_ended_at": "2026-10-02T09:00:00+00:00",
        "effective_started_at": None,
        "effective_ended_at": None,
        "source": "timer",
        "created_at": "2026-10-02T07:00:00+00:00",
        "updated_at": "2026-10-02T09:00:00+00:00",
        "deleted_at": None,
        "rounding_minutes": 5,
        "revision": 2,
        "testhuset_task_id": None,
        "dsb_allocation_id": None,
    }
    incoming = finalize_group(
        (
            replace(
                original.conflict.changes[0], entity_kind=key[0], entity_id=key[1], payload=payload
            ),
        )
    )[0]
    view = module.SyncConflictDialog(
        SyncConflictReview(
            SyncConflict("work-conflict", (key,), (incoming,), "invalid_aggregate"),
            {key: payload},
            {key: ()},
            "fingerprint",
            (),
        )
    )
    qtbot.addWidget(view)
    view.choices[key].setCurrentIndex(1)
    check, start, end = view.corrections[key]
    check.setChecked(True)
    start.set_value(datetime(2026, 10, 2, 7, tzinfo=UTC))
    end.set_value(datetime(2026, 10, 2, 8, tzinfo=UTC))
    with qtbot.waitSignal(view.resolution_requested) as emitted:
        view.save_button.click()
    corrected = emitted.args[2][key]
    assert corrected["actual_ended_at"] == "2026-10-02T08:00:00+00:00"
    assert corrected["effective_ended_at"] == corrected["actual_ended_at"]
    assert corrected["source"] == "manual"


def test_authorization_worker_keeps_tracking_responsive_and_rejects_duplicates(qtbot, rig):
    import threading

    from PySide6.QtCore import QThread
    from PySide6.QtWidgets import QApplication

    from qi_flow.application.dto import FinishWorkCommand, StartWorkCommand

    module = importlib.import_module("qi_flow.ui.google_sync_controller")
    started, release = threading.Event(), threading.Event()

    class Authorization:
        def authorize(self, *, cancelled, timeout_seconds):
            assert timeout_seconds == 120
            started.set()
            while not release.wait(0.01):
                if cancelled():
                    from qi_flow.application.sync_models import SyncJobCancelledError

                    raise SyncJobCancelledError("Cancelled")

    controller = module.GoogleSyncController()
    deliveries = []
    controller.completed.connect(
        lambda kind, value: deliveries.append((kind, QThread.currentThread()))
    )
    assert controller.authorize(Authorization())
    qtbot.waitUntil(started.is_set)
    assert not controller.authorize(Authorization())
    rig.service.start_work(StartWorkCommand())
    assert rig.service.active_state().session_id is not None
    from datetime import timedelta

    rig.clock.value += timedelta(minutes=1)
    rig.service.finish_work(FinishWorkCommand())
    release.set()
    qtbot.waitUntil(lambda: not controller.busy)
    assert len(deliveries) == 1
    assert deliveries[0] == ("authorize", QApplication.instance().thread())


def test_shutdown_cancels_authorization_waits_for_worker_and_discards_late_result(qtbot):
    import threading

    module = importlib.import_module("qi_flow.ui.google_sync_controller")
    started = threading.Event()
    controller = module.GoogleSyncController()
    deliveries = []
    controller.completed.connect(lambda *args: deliveries.append(args))

    def operation(cancelled):
        started.set()
        while not cancelled():
            threading.Event().wait(0.01)
        return "late success"

    assert controller.start("sync", operation)
    qtbot.waitUntil(started.is_set)
    with qtbot.waitSignal(controller.ready_for_shutdown):
        controller.begin_shutdown()
    assert not controller.busy
    assert deliveries == []
    assert not controller.start("sync", operation)


def test_cancelled_operation_reports_once_on_main_thread(qtbot):
    import threading

    module = importlib.import_module("qi_flow.ui.google_sync_controller")
    controller = module.GoogleSyncController()
    started = threading.Event()
    failures = []
    controller.failed.connect(lambda *args: failures.append(args))

    def operation(cancelled):
        started.set()
        while not cancelled():
            threading.Event().wait(0.01)

    assert controller.start("sync", operation)
    qtbot.waitUntil(started.is_set)
    controller.cancel()
    qtbot.waitUntil(lambda: not controller.busy)
    assert len(failures) == 1
    assert failures[0][0] == "sync"


def test_settings_authorization_is_cancellable_while_tracking_remains_available(qtbot, rig):
    import threading

    from qi_flow.application.dto import StartWorkCommand
    from qi_flow.application.google_sync import GoogleSyncSettings
    from qi_flow.application.sync_models import SyncJobCancelledError
    from qi_flow.ui.settings_page import SettingsPage

    started = threading.Event()

    class OAuth:
        def is_authorized(self):
            return False

        def authorize(self, *, cancelled, timeout_seconds):
            started.set()
            while not cancelled():
                threading.Event().wait(0.01)
            raise SyncJobCancelledError("Google authorization cancelled")

    page = SettingsPage(
        *rig.window_args, google_sync=GoogleSyncSettings(rig.uow, rig.clock), google_oauth=OAuth()
    )
    qtbot.addWidget(page)
    page._google_auth_button.click()
    qtbot.waitUntil(started.is_set)
    assert not page._google_auth_button.isEnabled()
    rig.service.start_work(StartWorkCommand())
    assert rig.service.active_state().session_id is not None
    page._google_cancel.click()
    qtbot.waitUntil(lambda: not page._google_controller.busy)
    assert "cancelled" in page._authorization_status.text()
    assert page._google_auth_button.isEnabled()


def test_settings_shows_pending_counts_and_retained_success_in_copenhagen(qtbot, rig):
    from qi_flow.application.google_sync import GoogleSyncSettings
    from qi_flow.application.google_sync_service import SyncResult
    from qi_flow.ui.settings_page import SettingsPage

    class OAuth:
        def is_authorized(self):
            return False

    page = SettingsPage(
        *rig.window_args, google_sync=GoogleSyncSettings(rig.uow, rig.clock), google_oauth=OAuth()
    )
    qtbot.addWidget(page)
    page._sync_completed(SyncResult(2, 1, datetime(2026, 10, 3, 12, tzinfo=UTC), "conflict"))
    assert "2 pending" in page._sync_status.text()
    assert "1 conflicts" in page._sync_status.text()
    assert "14:00 CEST" in page._sync_status.text()


def test_settings_guided_migration_uses_injected_application_actions(qtbot, rig):
    from qi_flow.application.dto import SyncStatusView
    from qi_flow.application.google_sync import GoogleSyncConfiguration, GoogleSyncSettings
    from qi_flow.application.sync_migration import MigrationPlan, MigrationStatus
    from qi_flow.application.sync_models import SyncTarget
    from qi_flow.ui.settings_page import SettingsPage

    settings = GoogleSyncSettings(rig.uow, rig.clock)
    settings.save(
        GoogleSyncConfiguration(
            "https://docs.google.com/spreadsheets/d/sheet/edit", "test.apps.googleusercontent.com"
        )
    )

    class OAuth:
        def is_authorized(self):
            return True

    class Actions:
        def status(self):
            return SyncStatusView("migration_required")

        def migrate(self, action, participants, participant, **kwargs):
            self.received = action, participants, participant, kwargs["writers_paused"]
            return MigrationStatus(
                MigrationPlan(
                    SyncTarget("sheet", "log"),
                    tuple(sorted(participants)),
                    "fingerprint",
                    (),
                    rig.clock.now(),
                ),
                (),
                False,
            )

    actions = Actions()
    page = SettingsPage(
        *rig.window_args, google_sync=settings, google_oauth=OAuth(), sync_actions=actions
    )
    qtbot.addWidget(page)
    page._sync_migration_button.click()
    view = page._migration_dialog
    view.roster_edit.setPlainText("office\nhome")
    view.participant_edit.setText("office")
    view.paused_check.setChecked(True)
    view.review_button.click()
    qtbot.waitUntil(lambda: not page._google_controller.busy)
    assert actions.received == ("begin", ("office", "home"), "office", True)
    assert "home" in view.status_label.text()
    view.reject()


def test_settings_conflict_review_cancels_without_writes_and_saves_explicit_choice(qtbot, rig):
    from qi_flow.application.dto import SyncStatusView
    from qi_flow.application.google_sync import GoogleSyncSettings
    from qi_flow.ui.settings_page import SettingsPage

    class OAuth:
        def is_authorized(self):
            return True

    class Actions:
        def __init__(self):
            self.saved = []

        def status(self):
            return SyncStatusView("conflict", conflict_count=1, conflicts=(reviewed().conflict,))

        def review(self, identifier):
            return reviewed()

        def resolve(self, *args):
            self.saved.append(args)

    actions = Actions()
    page = SettingsPage(
        *rig.window_args,
        google_sync=GoogleSyncSettings(rig.uow, rig.clock),
        google_oauth=OAuth(),
        sync_actions=actions,
    )
    qtbot.addWidget(page)
    page._sync_review_button.click()
    page._conflict_dialog.reject()
    assert actions.saved == []
    page._sync_review_button.click()
    page._conflict_dialog.choices[KEY].setCurrentIndex(1)
    page._conflict_dialog.save_button.click()
    qtbot.waitUntil(lambda: not page._google_controller.busy)
    assert actions.saved[0][1] == frozenset({"a", "b"})
    assert actions.saved[0][2][KEY]["note"] == "local note"


def test_offline_status_survives_worker_cleanup_without_changing_last_success(qtbot, rig):
    from qi_flow.application.dto import SyncStatusView
    from qi_flow.application.google_sync import GoogleSyncSettings
    from qi_flow.ui.settings_page import SettingsPage

    class OAuth:
        def is_authorized(self):
            return True

    class Actions:
        def status(self):
            return SyncStatusView(
                "pending", pending_count=2, last_success=datetime(2026, 10, 3, 12, tzinfo=UTC)
            )

    page = SettingsPage(
        *rig.window_args,
        google_sync=GoogleSyncSettings(rig.uow, rig.clock),
        google_oauth=OAuth(),
        sync_actions=Actions(),
    )
    qtbot.addWidget(page)
    page._google_operation_failed("sync", OSError("synthetic network refusal"))
    page._google_busy_changed(False)
    assert "Offline" in page._sync_status.text()
    assert "2 pending" in page._sync_status.text()
    assert "14:00 CEST" in page._sync_status.text()
