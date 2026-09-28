"""Explicit starting times use persisted rules and the service clock."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from qi_flow.application.dto import (
    FinishWorkCommand,
    StartWorkCommand,
    UpdateActiveWorkStartCommand,
)
from qi_flow.application.time_tracking import TimeTrackingApplicationService
from qi_flow.domain.errors import DomainError
from qi_flow.domain.models import EntrySource
from qi_flow.infrastructure.sqlite.database import SQLiteDatabase
from qi_flow.infrastructure.sqlite.repositories import SQLiteUnitOfWork
from qi_flow.infrastructure.system import UuidIdentifierGenerator


@pytest.fixture
def rig(tmp_path):
    class Clock:
        value = datetime(2026, 9, 28, 10, 15, tzinfo=UTC)

        def now(self):
            return self.value

    database = SQLiteDatabase(tmp_path / "start.sqlite3")
    database.initialize()
    clock = Clock()
    service = TimeTrackingApplicationService(
        lambda: SQLiteUnitOfWork(database), clock, UuidIdentifierGenerator()
    )
    return SimpleNamespace(service=service, clock=clock)


def test_specified_start_counts_elapsed_and_keeps_exact_time(rig):
    from qi_flow.application.dto import StartWorkAtCommand

    start = rig.clock.value - timedelta(hours=2, minutes=13)
    rig.service.start_work_at(StartWorkAtCommand(start))
    assert rig.service.active_state().net_seconds == 7980
    session = rig.service.active_session_for_day(start.date())
    assert session.actual_started_at == start
    assert session.source is EntrySource.MANUAL
    assert session.rounding_minutes == 1
    assert rig.service.can_undo_timer_action()
    rig.service.undo_last_timer_action()
    assert rig.service.active_state().session_id is None


def test_specified_start_rejects_future_and_overlap_without_persisting(rig):
    from qi_flow.application.dto import StartWorkAtCommand

    with pytest.raises(DomainError):
        rig.service.start_work_at(StartWorkAtCommand(rig.clock.value + timedelta(minutes=1)))
    rig.service.start_work(StartWorkCommand(rig.clock.value - timedelta(hours=2)))
    rig.service.finish_work(FinishWorkCommand(rig.clock.value - timedelta(hours=1)))
    with pytest.raises(DomainError):
        rig.service.start_work_at(StartWorkAtCommand(rig.clock.value - timedelta(hours=3)))
    assert rig.service.active_state().session_id is None


def test_specified_start_uses_copenhagen_date_and_injected_clock(rig):
    from qi_flow.application.dto import StartWorkAtCommand

    rig.clock.value = datetime(2026, 9, 28, 22, 30, tzinfo=UTC)
    rig.service.start_work_at(StartWorkAtCommand(rig.clock.value - timedelta(minutes=20)))
    assert rig.service.today_summary().work_date.day == 29
    assert rig.service.today_summary().net_seconds == 1200


@pytest.mark.parametrize("blocking", ["sleep", "midnight"])
def test_active_start_command_rechecks_recovery_before_persisting(rig, blocking):
    start = rig.clock.value - timedelta(hours=2)
    rig.service.start_work(StartWorkCommand(start))
    session_id = rig.service.active_state().session_id
    if blocking == "sleep":
        rig.service.detect_sleep_gap(rig.clock.value - timedelta(hours=1), rig.clock.value)
    else:
        rig.clock.value += timedelta(days=1)
    with pytest.raises(DomainError):
        rig.service.update_active_work_start(
            UpdateActiveWorkStartCommand(session_id, start - timedelta(minutes=23))
        )
    assert rig.service.active_state().actual_started_at == start
