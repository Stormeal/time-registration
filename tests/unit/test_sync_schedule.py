"""Automatic scheduling uses durable eligibility and a bounded retry policy."""

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


def test_open_and_five_minute_periodic_checks(rig):
    clock, schedule, state = rig
    current = state()
    assert schedule.due(current, busy=False)
    schedule.started(current)
    assert not schedule.due(current, busy=True)
    schedule.finished(current.binding, succeeded=True)
    clock.value += timedelta(minutes=5) - timedelta(microseconds=1)
    assert not schedule.due(current, busy=False)
    clock.value += timedelta(microseconds=1)
    assert schedule.due(current, busy=False)


def test_new_eligible_edits_coalesce_during_a_job_without_ack_echo(rig):
    _, schedule, state = rig
    first = state(["one"])
    schedule.started(first)
    assert not schedule.due(state(["one", "two", "three"]), busy=True)
    schedule.finished(first.binding, succeeded=True)
    second = state(["two", "three"])
    assert schedule.due(second, busy=False)
    schedule.started(second)
    schedule.finished(second.binding, succeeded=True)
    assert not schedule.due(state(), busy=False)


def test_offline_and_cancelled_jobs_back_off_even_with_new_edits(rig):
    clock, schedule, state = rig
    current = state(["one"])
    schedule.started(current)
    schedule.finished(current.binding, succeeded=False)
    clock.value += timedelta(seconds=14)
    assert not schedule.due(state(["one", "two"]), busy=False)
    clock.value += timedelta(seconds=1)
    assert schedule.due(state(["one", "two"]), busy=False)
    schedule.started(state(["one", "two"]))
    schedule.finished(current.binding, succeeded=False)
    clock.value += timedelta(seconds=29)
    assert not schedule.due(current, busy=False)
    clock.value += timedelta(seconds=1)
    assert schedule.due(current, busy=False)


def test_backoff_caps_at_five_minutes_but_honors_longer_retry_after(rig):
    clock, schedule, state = rig
    current = state()
    for _ in range(10):
        schedule.started(current)
        schedule.finished(current.binding, succeeded=False)
        clock.value += timedelta(minutes=5)
        assert schedule.due(current, busy=False)
    schedule.started(current)
    schedule.finished(current.binding, succeeded=False, retry_after=900)
    clock.value += timedelta(seconds=899)
    assert not schedule.due(current, busy=False)
    clock.value += timedelta(seconds=1)
    assert schedule.due(current, busy=False)


def test_disable_disconnect_and_unreviewed_migration_never_schedule(rig):
    _, schedule, state = rig
    assert not schedule.due(state(enabled=False), busy=False)
    current = state()
    assert schedule.due(current, busy=False)
    schedule.started(current)
    assert not schedule.due(state(enabled=False), busy=False)
    schedule.finished(current.binding, succeeded=True)
    assert schedule.due(state(generation=2), busy=False)


def test_resume_detects_overdue_work_and_obsolete_result_cannot_delay_new_binding(rig):
    clock, schedule, state = rig
    old = state()
    schedule.started(old)
    assert not schedule.due(state(generation=2), busy=True)
    schedule.finished(old.binding, succeeded=False, retry_after=1000)
    new = state(generation=2)
    assert schedule.due(new, busy=False)
    schedule.started(new)
    schedule.finished(new.binding, succeeded=True)
    clock.value += timedelta(hours=2)
    assert schedule.due(new, busy=False)


def test_closing_retains_pending_work_and_never_starts_a_two_minute_job(rig):
    _, schedule, state = rig
    schedule.close()
    assert not schedule.due(state(["pending"]), busy=False)
