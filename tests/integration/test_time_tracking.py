"""End-to-end persistence tests for the P0 tracking slice."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from qi_flow.application.dto import (
    FinishDeductionCommand,
    FinishWorkCommand,
    StartDeductionCommand,
    StartWorkCommand,
)
from qi_flow.application.time_tracking import TimeTrackingApplicationService
from qi_flow.domain.errors import InvalidIntervalError, RecoveryRequiredError
from qi_flow.domain.models import DeductionId, DeductionKind, SessionId
from qi_flow.infrastructure.sqlite.database import SQLiteDatabase
from qi_flow.infrastructure.sqlite.repositories import SQLiteUnitOfWork


class FixedClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


class FixedIds:
    def __init__(self) -> None:
        self.session_count = 0
        self.deduction_count = 0

    def session_id(self) -> SessionId:
        self.session_count += 1
        return SessionId(f"session-{self.session_count}")

    def deduction_id(self) -> DeductionId:
        self.deduction_count += 1
        return DeductionId(f"deduction-{self.deduction_count}")


def build_service(
    tmp_path: Path, at: datetime
) -> tuple[TimeTrackingApplicationService, FixedClock, SQLiteDatabase]:
    database = SQLiteDatabase(tmp_path / "qi-flow.sqlite3")
    database.initialize()
    clock = FixedClock(at)
    service = TimeTrackingApplicationService(lambda: SQLiteUnitOfWork(database), clock, FixedIds())
    return service, clock, database


def test_work_and_multiple_lunches_persist_with_actual_and_rounded_boundaries(
    tmp_path: Path,
) -> None:
    at = datetime(2026, 9, 15, 7, 2, tzinfo=UTC)
    service, clock, database = build_service(tmp_path, at)

    service.start_work(StartWorkCommand())
    clock.value += timedelta(hours=3, minutes=1)
    service.start_deduction(StartDeductionCommand(DeductionKind.LUNCH))
    clock.value += timedelta(minutes=31)
    state = service.finish_deduction(FinishDeductionCommand())
    assert state.net_seconds == 3 * 3600 + 60
    clock.value += timedelta(hours=4, minutes=26)
    service.finish_work(FinishWorkCommand())

    with SQLiteUnitOfWork(database) as uow:
        session = uow.sessions.get(SessionId("session-1"))
        deductions = uow.deductions.list_for_session(SessionId("session-1"))
    assert session is not None
    assert session.actual_started_at == at
    assert session.effective_started_at == datetime(2026, 9, 15, 7, 0, tzinfo=UTC)
    assert session.effective_ended_at == datetime(2026, 9, 15, 15, 0, tzinfo=UTC)
    assert deductions[0].effective_started_at == datetime(2026, 9, 15, 10, 5, tzinfo=UTC)
    assert deductions[0].effective_ended_at == datetime(2026, 9, 15, 10, 35, tzinfo=UTC)


def test_zero_length_rounded_lunch_rolls_back(tmp_path: Path) -> None:
    at = datetime(2026, 9, 15, 7, 2, tzinfo=UTC)
    service, clock, _ = build_service(tmp_path, at)
    service.start_work(StartWorkCommand())
    clock.value += timedelta(hours=1)
    service.start_deduction(StartDeductionCommand(DeductionKind.LUNCH))
    clock.value += timedelta(seconds=20)

    with pytest.raises(InvalidIntervalError):
        service.finish_deduction(FinishDeductionCommand())

    assert service.active_state().active_deduction_kind is DeductionKind.LUNCH


def test_previous_day_recovery_blocks_then_can_continue(tmp_path: Path) -> None:
    start = datetime(2026, 9, 15, 21, 0, tzinfo=UTC)
    service, clock, _ = build_service(tmp_path, start)
    service.start_work(StartWorkCommand())
    clock.value = datetime(2026, 9, 16, 7, 0, tzinfo=UTC)

    assert service.recovery_state() is not None
    with pytest.raises(RecoveryRequiredError):
        service.start_deduction(StartDeductionCommand(DeductionKind.LUNCH))
    service.continue_recovery()
    assert service.recovery_state() is None
    service.start_deduction(StartDeductionCommand(DeductionKind.LUNCH))


def test_changing_rounding_only_affects_future_actions(tmp_path: Path) -> None:
    at = datetime(2026, 9, 15, 7, 2, tzinfo=UTC)
    service, clock, database = build_service(tmp_path, at)
    service.start_work(StartWorkCommand())
    service.set_rounding_minutes(15)
    clock.value += timedelta(minutes=14)
    service.finish_work(FinishWorkCommand())
    clock.value += timedelta(minutes=1)
    service.start_work(StartWorkCommand())
    clock.value += timedelta(minutes=6)
    service.finish_work(FinishWorkCommand())

    with SQLiteUnitOfWork(database) as uow:
        first = uow.sessions.get(SessionId("session-1"))
        second = uow.sessions.get(SessionId("session-2"))
    assert first is not None and second is not None
    assert first.rounding_minutes == 5
    assert second.rounding_minutes == 15


def test_start_and_finish_timer_actions_can_be_undone_for_30_seconds(tmp_path: Path) -> None:
    at = datetime(2026, 9, 15, 7, 0, tzinfo=UTC)
    service, clock, _ = build_service(tmp_path, at)
    service.start_work(StartWorkCommand())
    assert service.can_undo_timer_action()
    assert service.undo_last_timer_action().session_id is None

    service.start_work(StartWorkCommand())
    clock.value += timedelta(hours=8)
    service.finish_work(FinishWorkCommand())
    recovered = service.undo_last_timer_action()
    assert recovered.session_id == SessionId("session-2")
