"""Automatic sync is requested only on opening and after committed work finishes."""

import importlib
from datetime import UTC, datetime, timedelta

import pytest

from qi_flow.application.sync_models import SyncTarget

NOW = datetime(2026, 10, 3, 12, tzinfo=UTC)


@pytest.fixture
def rig():
    module = importlib.import_module("qi_flow.application.sync_schedule")

    class Clock:
        value = NOW

        def now(self):
            return self.value

    clock = Clock()
    schedule = module.SyncSchedule(clock)

    def state(ids=(), generation=1, enabled=True):
        return module.SyncScheduleState(
            (SyncTarget("sheet", "log"), generation) if enabled else None, frozenset(ids)
        )

    return clock, schedule, state


def test_open_syncs_once_without_periodic_or_edit_triggers(rig):
    clock, schedule, state = rig
    current = state()
    assert schedule.due(current, busy=False)
    schedule.started(current)
    schedule.finished(current.binding, succeeded=True)

    clock.value += timedelta(hours=8)
    assert not schedule.due(state(["new-edit"]), busy=False)
    assert schedule.delay_seconds() is None


def test_finish_waits_for_undo_grace_and_coalesces_repeated_finishes(rig):
    clock, schedule, state = rig
    current = state()
    schedule.started(current)
    schedule.finished(current.binding, succeeded=True)
    schedule.work_finished()
    assert schedule.delay_seconds() == 30
    clock.value += timedelta(seconds=20)
    schedule.work_finished()
    clock.value += timedelta(seconds=29)
    assert not schedule.due(state(["completed"]), busy=False)
    clock.value += timedelta(seconds=1)
    assert schedule.due(state(["completed"]), busy=False)
    schedule.started(state(["completed"]))
    assert schedule.delay_seconds() is None


def test_finish_requested_during_a_job_survives_its_completion(rig):
    clock, schedule, state = rig
    current = state()
    schedule.started(current)
    schedule.work_finished()
    clock.value += timedelta(seconds=30)
    assert not schedule.due(current, busy=True)
    schedule.finished(current.binding, succeeded=True)
    assert schedule.due(current, busy=False)


def test_failure_waits_for_next_trigger_and_honors_server_cooldown(rig):
    clock, schedule, state = rig
    current = state()
    schedule.started(current)
    schedule.finished(current.binding, succeeded=False, retry_after=900)
    assert schedule.delay_seconds() is None
    clock.value += timedelta(seconds=10)
    schedule.work_finished()
    assert schedule.delay_seconds() == 890
    clock.value += timedelta(seconds=889)
    assert not schedule.due(current, busy=False)
    clock.value += timedelta(seconds=1)
    assert schedule.due(current, busy=False)


def test_disable_and_reconfiguration_never_create_a_new_request(rig):
    _, schedule, state = rig
    assert not schedule.due(state(enabled=False), busy=False)
    current = state()
    assert schedule.due(current, busy=False)
    schedule.started(current)
    schedule.finished(current.binding, succeeded=True)
    assert not schedule.due(state(generation=2), busy=False)
    assert not schedule.due(state(enabled=False), busy=False)


def test_close_discards_scheduled_attempt_and_resume_requests_open_sync(rig):
    _, schedule, state = rig
    current = state()
    schedule.started(current)
    schedule.work_finished()
    schedule.close()
    assert schedule.delay_seconds() is None
    assert not schedule.due(current, busy=False)
    schedule.work_finished()
    assert not schedule.due(current, busy=False)
    schedule.resume()
    assert schedule.due(current, busy=False)


def test_obsolete_failure_cannot_delay_a_current_finish_request(rig):
    clock, schedule, state = rig
    old = state()
    schedule.started(old)
    new = state(generation=2)
    schedule.work_finished()
    assert not schedule.due(new, busy=True)
    schedule.finished(old.binding, succeeded=False, retry_after=1000)
    clock.value += timedelta(seconds=30)
    assert schedule.due(new, busy=False)


def test_unrepresentable_server_delay_cannot_crash_requested_sync(rig):
    clock, schedule, state = rig
    current = state()
    schedule.started(current)
    schedule.finished(current.binding, succeeded=False, retry_after=1e100)
    schedule.work_finished()
    assert schedule.delay_seconds() > 0
    clock.value += timedelta(days=1)
    assert not schedule.due(current, busy=False)
