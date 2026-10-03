"""Daily backup policy keys every attempt to the captured Copenhagen date and folder."""

import importlib
from datetime import UTC, date, datetime, timedelta

import pytest


@pytest.fixture
def rig():
    module = importlib.import_module("qi_flow.application.backup_schedule")

    class Clock:
        value = datetime(2026, 10, 3, 21, 59, tzinfo=UTC)

        def now(self):
            return self.value

    clock, destination = Clock(), ["folder-a"]
    schedule = module.BackupSchedule(clock, lambda: destination[0])
    return schedule, clock, destination


def test_startup_once_per_day_and_midnight_while_open(rig):
    schedule, clock, _ = rig
    assert schedule.due()
    schedule.record_attempt("folder-a", date(2026, 10, 3), succeeded=True)
    assert not schedule.due()
    clock.value += timedelta(minutes=1)
    assert schedule.due()  # Copenhagen midnight, not UTC midnight.
    schedule.record_attempt("folder-a", date(2026, 10, 4), succeeded=True)
    assert not schedule.due()


def test_failure_retries_at_most_once_per_fifteen_minutes_and_success_clears(rig):
    schedule, clock, _ = rig
    clock.value = datetime(2026, 10, 3, 12, tzinfo=UTC)
    schedule.record_attempt("folder-a", date(2026, 10, 3), succeeded=False)
    clock.value += timedelta(seconds=899)
    assert not schedule.due()
    clock.value += timedelta(seconds=1)
    assert schedule.due()
    schedule.record_attempt("folder-a", date(2026, 10, 3), succeeded=True)
    clock.value += timedelta(hours=1)
    assert not schedule.due()


def test_folder_change_and_old_completion_do_not_mark_new_folder_or_day_successful(rig):
    schedule, clock, destination = rig
    destination[0] = "folder-b"
    schedule.record_attempt("folder-a", date(2026, 10, 3), succeeded=True)
    assert schedule.due()
    schedule.record_attempt("folder-b", date(2026, 10, 3), succeeded=True)
    clock.value += timedelta(days=2)
    assert schedule.due()  # Sleep/resume checks today's date.
    schedule.record_attempt("folder-b", date(2026, 10, 3), succeeded=True)
    assert schedule.due()
