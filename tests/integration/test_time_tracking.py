"""End-to-end persistence tests for the P0 tracking slice."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from qi_flow.application.dto import (
    FinishDeductionCommand,
    FinishWorkCommand,
    ManualDeductionCommand,
    ManualWorkSessionCommand,
    StartDeductionCommand,
    StartWorkCommand,
    UpdateDayDetailsCommand,
)
from qi_flow.application.time_tracking import TimeTrackingApplicationService
from qi_flow.domain.errors import (
    InvalidIntervalError,
    OverlappingIntervalError,
    RecoveryRequiredError,
)
from qi_flow.domain.models import DeductionId, DeductionKind, SessionId, WorkLocation
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

    def audit_id(self) -> str:
        return f"audit-{self.session_count}-{self.deduction_count}"


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


def test_manual_entries_are_exact_and_reject_overlap_or_orphan_lunch(tmp_path: Path) -> None:
    now = datetime(2026, 9, 15, 18, 0, tzinfo=UTC)
    service, _, _ = build_service(tmp_path, now)
    session = service.add_manual_session(
        ManualWorkSessionCommand(
            datetime(2026, 9, 15, 7, 1, tzinfo=UTC),
            datetime(2026, 9, 15, 15, 2, tzinfo=UTC),
        )
    )
    lunch = service.add_manual_deduction(
        ManualDeductionCommand(
            session.id,
            DeductionKind.LUNCH,
            datetime(2026, 9, 15, 11, 59, tzinfo=UTC),
            datetime(2026, 9, 15, 12, 23, tzinfo=UTC),
        )
    )
    assert lunch.effective_started_at is not None and lunch.effective_started_at.minute == 59
    with pytest.raises(OverlappingIntervalError):
        service.add_manual_session(
            ManualWorkSessionCommand(
                datetime(2026, 9, 15, 14, 0, tzinfo=UTC),
                datetime(2026, 9, 15, 16, 0, tzinfo=UTC),
            )
        )
    with pytest.raises(InvalidIntervalError):
        service.add_manual_deduction(
            ManualDeductionCommand(
                session.id,
                DeductionKind.LUNCH,
                datetime(2026, 9, 15, 15, 0, tzinfo=UTC),
                datetime(2026, 9, 15, 15, 30, tzinfo=UTC),
            )
        )


def test_day_context_persists_multiline_unicode(tmp_path: Path) -> None:
    now = datetime(2026, 9, 15, 18, 0, tzinfo=UTC)
    service, _, database = build_service(tmp_path, now)
    service.update_day_details(
        UpdateDayDetailsCommand(date(2026, 9, 15), WorkLocation.OFFICE, "DSB\nKøbenhavn")
    )
    with SQLiteUnitOfWork(database) as uow:
        details = uow.days.get(date(2026, 9, 15))
    assert details is not None
    assert details.location is WorkLocation.OFFICE
    assert details.note == "DSB\nKøbenhavn"


def test_deleted_manual_session_can_be_restored_from_30_day_audit(tmp_path: Path) -> None:
    now = datetime(2026, 9, 15, 18, 0, tzinfo=UTC)
    service, _, _ = build_service(tmp_path, now)
    session = service.add_manual_session(
        ManualWorkSessionCommand(
            datetime(2026, 9, 15, 7, 0, tzinfo=UTC), datetime(2026, 9, 15, 15, 0, tzinfo=UTC)
        )
    )
    service.delete_work_session(session.id)
    restored = service.restore_work_session(session.id)
    assert restored.deleted_at is None
    assert restored.actual_ended_at == datetime(2026, 9, 15, 15, 0, tzinfo=UTC)


def test_long_sleep_requires_resolution_and_can_be_excluded_as_break(tmp_path: Path) -> None:
    start = datetime(2026, 9, 15, 7, 0, tzinfo=UTC)
    service, clock, database = build_service(tmp_path, start)
    service.start_work(StartWorkCommand())
    gap = service.detect_sleep_gap(
        start + timedelta(hours=2), start + timedelta(hours=2, minutes=31)
    )
    assert gap is not None
    with pytest.raises(RecoveryRequiredError):
        service.finish_work(FinishWorkCommand(start + timedelta(hours=4)))
    clock.value = start + timedelta(hours=4)
    service.resolve_sleep_gap("exclude")
    with SQLiteUnitOfWork(database) as uow:
        deductions = uow.deductions.list_for_session(SessionId("session-1"))
    assert len(deductions) == 1
    assert deductions[0].kind is DeductionKind.SLEEP_BREAK
    assert deductions[0].source.value == "recovery"
