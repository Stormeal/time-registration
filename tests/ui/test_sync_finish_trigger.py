"""Opening and committed Finish share one worker across window and tray actions."""

from datetime import timedelta
from threading import Event

import pytest

from qi_flow.application.sync_models import SyncTarget
from qi_flow.application.sync_schedule import SyncSchedule, SyncScheduleState
from qi_flow.application.time_tracking import TimeTrackingApplicationService
from qi_flow.infrastructure.system import UuidIdentifierGenerator
from qi_flow.ui.google_sync_controller import AutomaticSyncController, GoogleSyncController
from qi_flow.ui.today_page import TodayPage
from qi_flow.ui.tray_panel import TrayPanel


class Actions:
    calls = 0

    def schedule_state(self):
        return SyncScheduleState((SyncTarget("sheet", "log"), 1))

    def synchronize(self, cancelled):
        self.calls += 1


@pytest.mark.parametrize("route", ["today", "tray"])
def test_start_does_not_sync_and_finish_schedules_after_undo_from_each_surface(qtbot, rig, route):
    actions, worker = Actions(), GoogleSyncController()
    auto = AutomaticSyncController(actions, worker, SyncSchedule(rig.clock))
    service = TimeTrackingApplicationService(
        rig.uow,
        rig.clock,
        UuidIdentifierGenerator(),
        on_work_finished=auto.work_finished,
    )
    page = TodayPage(service) if route == "today" else TrayPanel(service, lambda: None)
    qtbot.addWidget(page)
    qtbot.waitUntil(lambda: actions.calls == 1 and not worker.busy)

    page._start_work.click()
    assert service.active_state().session_id is not None
    assert actions.calls == 1
    assert not auto._timer.isActive()

    rig.clock.value += timedelta(minutes=5)
    page.refresh()
    page._finish_work.click()
    assert service.active_state().session_id is None
    assert actions.calls == 1
    rig.clock.value += timedelta(seconds=29)
    auto.poll()
    assert actions.calls == 1
    rig.clock.value += timedelta(seconds=1)
    auto.poll()
    qtbot.waitUntil(lambda: actions.calls == 2 and not worker.busy)
    assert not auto._timer.isActive()
    auto.begin_shutdown()


def test_finish_requested_during_opening_runs_when_owned_worker_becomes_free(qtbot, rig):
    release = Event()

    class BlockingActions(Actions):
        def synchronize(self, cancelled):
            self.calls += 1
            if self.calls == 1:
                assert release.wait(2)

    actions, worker = BlockingActions(), GoogleSyncController()
    auto = AutomaticSyncController(actions, worker, SyncSchedule(rig.clock))
    qtbot.waitUntil(lambda: actions.calls == 1)
    auto.work_finished()
    rig.clock.value += timedelta(seconds=30)
    assert worker.busy
    release.set()
    qtbot.waitUntil(lambda: actions.calls == 2 and not worker.busy)
    assert not auto._timer.isActive()
    auto.begin_shutdown()


def test_manual_sync_during_undo_grace_preserves_the_delayed_finish_request(qtbot, rig):
    actions, worker = Actions(), GoogleSyncController()
    auto = AutomaticSyncController(actions, worker, SyncSchedule(rig.clock))
    qtbot.waitUntil(lambda: actions.calls == 1 and not worker.busy)
    auto.work_finished()
    assert worker.start("sync", actions.synchronize)
    qtbot.waitUntil(lambda: actions.calls == 2 and not worker.busy)
    assert auto._timer.isActive()

    rig.clock.value += timedelta(seconds=30)
    auto.poll()
    qtbot.waitUntil(lambda: actions.calls == 3 and not worker.busy)
    assert not auto._timer.isActive()
    auto.begin_shutdown()
