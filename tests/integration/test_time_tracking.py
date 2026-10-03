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
    ReminderSettingsView,
    StartDeductionCommand,
    StartWorkCommand,
    UpdateActiveWorkStartCommand,
    UpdateDayDetailsCommand,
    UpdateDeductionCommand,
    UpdateWorkSessionCommand,
)
from qi_flow.application.time_tracking import TimeTrackingApplicationService
from qi_flow.domain.errors import (
    DomainError,
    InvalidIntervalError,
    InvalidStateTransitionError,
    OverlappingIntervalError,
    RecoveryRequiredError,
)
from qi_flow.domain.models import (
    Deduction,
    DeductionId,
    DeductionKind,
    IsoWeek,
    SessionId,
    WorkLocation,
)
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
        self.audit_count = 0

    def session_id(self) -> SessionId:
        self.session_count += 1
        return SessionId(f"session-{self.session_count}")

    def deduction_id(self) -> DeductionId:
        self.deduction_count += 1
        return DeductionId(f"deduction-{self.deduction_count}")

    def audit_id(self) -> str:
        self.audit_count += 1
        return f"audit-{self.audit_count}"


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


def test_running_session_start_can_be_corrected_without_stopping_timer(tmp_path: Path) -> None:
    now = datetime(2026, 9, 15, 9, 0, tzinfo=UTC)
    service, _, database = build_service(tmp_path, now)
    service.start_work(StartWorkCommand())

    service.update_active_work_start(
        UpdateActiveWorkStartCommand(
            SessionId("session-1"), datetime(2026, 9, 15, 8, 15, tzinfo=UTC)
        )
    )

    with SQLiteUnitOfWork(database) as uow:
        session = uow.sessions.get(SessionId("session-1"))
    assert session is not None
    assert session.is_active
    assert session.actual_started_at == datetime(2026, 9, 15, 8, 15, tzinfo=UTC)
    assert session.effective_started_at is None
    assert session.effective_ended_at is None
    assert session.rounding_minutes == 1


def test_short_rounded_lunch_finishes_with_its_actual_known_duration(tmp_path: Path) -> None:
    at = datetime(2026, 9, 15, 7, 2, tzinfo=UTC)
    service, clock, database = build_service(tmp_path, at)
    service.start_work(StartWorkCommand())
    clock.value += timedelta(hours=1)
    service.start_deduction(StartDeductionCommand(DeductionKind.LUNCH))
    clock.value += timedelta(seconds=20)

    state = service.finish_deduction(FinishDeductionCommand())

    assert state.active_deduction_kind is None
    with SQLiteUnitOfWork(database) as uow:
        deduction = uow.deductions.list_for_session(SessionId("session-1"))[0]
    assert deduction.actual_ended_at == clock.value
    assert deduction.effective_started_at == deduction.actual_started_at
    assert deduction.effective_ended_at == clock.value


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


def test_timer_work_rounds_start_down_and_finish_up(tmp_path: Path) -> None:
    service, clock, database = build_service(tmp_path, datetime(2026, 9, 15, 7, 5, tzinfo=UTC))
    service.set_rounding_minutes(15)
    service.start_work(StartWorkCommand())
    clock.value = datetime(2026, 9, 15, 16, 10, tzinfo=UTC)

    service.finish_work(FinishWorkCommand())

    with SQLiteUnitOfWork(database) as uow:
        session = uow.sessions.get(SessionId("session-1"))
    assert session is not None
    assert session.actual_started_at == datetime(2026, 9, 15, 7, 5, tzinfo=UTC)
    assert session.actual_ended_at == datetime(2026, 9, 15, 16, 10, tzinfo=UTC)
    assert session.effective_started_at == datetime(2026, 9, 15, 7, 0, tzinfo=UTC)
    assert session.effective_ended_at == datetime(2026, 9, 15, 16, 15, tzinfo=UTC)


def test_timer_start_nearer_next_boundary_persists_actual_start(tmp_path: Path) -> None:
    start = datetime(2026, 10, 2, 6, 40, tzinfo=UTC)
    service, clock, database = build_service(tmp_path, start)
    service.set_rounding_minutes(15)
    service.start_work(StartWorkCommand())
    clock.value = datetime(2026, 10, 2, 7, 0, tzinfo=UTC)

    service.finish_work(FinishWorkCommand())

    with SQLiteUnitOfWork(database) as uow:
        session = uow.sessions.get(SessionId("session-1"))
    assert session is not None
    assert session.effective_started_at == start
    assert session.effective_ended_at == clock.value


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


def test_lunch_timer_actions_can_be_undone_for_30_seconds(tmp_path: Path) -> None:
    start = datetime(2026, 9, 15, 7, 0, tzinfo=UTC)
    service, clock, database = build_service(tmp_path, start)
    service.start_work(StartWorkCommand())
    service.start_deduction(StartDeductionCommand(DeductionKind.LUNCH))
    assert service.undo_last_timer_action().active_deduction_kind is None

    service.start_deduction(StartDeductionCommand(DeductionKind.LUNCH))
    clock.value += timedelta(minutes=30)
    service.finish_deduction(FinishDeductionCommand())
    assert service.undo_last_timer_action().active_deduction_kind is DeductionKind.LUNCH
    with SQLiteUnitOfWork(database) as uow:
        active = uow.deductions.get_active(SessionId("session-1"))
    assert active is not None
    assert active.actual_ended_at is None


def test_timer_undo_expires_after_30_seconds(tmp_path: Path) -> None:
    service, clock, _ = build_service(tmp_path, datetime(2026, 9, 15, 7, 0, tzinfo=UTC))
    service.start_work(StartWorkCommand())
    clock.value += timedelta(seconds=31)

    assert not service.can_undo_timer_action()
    with pytest.raises(InvalidStateTransitionError):
        service.undo_last_timer_action()


def test_history_restores_the_exact_selected_edited_version(tmp_path: Path) -> None:
    now = datetime(2026, 9, 15, 18, 0, tzinfo=UTC)
    service, _, _ = build_service(tmp_path, now)
    session = service.add_manual_session(
        ManualWorkSessionCommand(
            datetime(2026, 9, 15, 7, 0, tzinfo=UTC),
            datetime(2026, 9, 15, 15, 0, tzinfo=UTC),
        )
    )
    service.update_work_session(
        UpdateWorkSessionCommand(
            session.id,
            datetime(2026, 9, 15, 8, 0, tzinfo=UTC),
            datetime(2026, 9, 15, 16, 0, tzinfo=UTC),
        )
    )

    history = service.entry_history_for_day(date(2026, 9, 15))
    assert len(history) == 1
    restored = service.restore_history_entry(history[0].audit_id)

    assert restored.actual_started_at == datetime(2026, 9, 15, 7, 0, tzinfo=UTC)
    assert restored.actual_ended_at == datetime(2026, 9, 15, 15, 0, tzinfo=UTC)
    assert len(service.entry_history_for_day(date(2026, 9, 15))) == 2


def test_restored_history_revalidates_overlap_before_applying(tmp_path: Path) -> None:
    now = datetime(2026, 9, 15, 18, 0, tzinfo=UTC)
    service, _, _ = build_service(tmp_path, now)
    first = service.add_manual_session(
        ManualWorkSessionCommand(
            datetime(2026, 9, 15, 7, 0, tzinfo=UTC),
            datetime(2026, 9, 15, 11, 0, tzinfo=UTC),
        )
    )
    service.update_work_session(
        UpdateWorkSessionCommand(
            first.id,
            datetime(2026, 9, 15, 7, 0, tzinfo=UTC),
            datetime(2026, 9, 15, 9, 0, tzinfo=UTC),
        )
    )
    second = service.add_manual_session(
        ManualWorkSessionCommand(
            datetime(2026, 9, 15, 10, 0, tzinfo=UTC),
            datetime(2026, 9, 15, 12, 0, tzinfo=UTC),
        )
    )
    history = service.entry_history_for_day(date(2026, 9, 15))
    first_version = next(entry for entry in history if entry.entity_id == str(first.id))

    with pytest.raises(OverlappingIntervalError):
        service.restore_history_entry(first_version.audit_id)
    current = service.completed_sessions_for_day(date(2026, 9, 15))
    assert next(item for item in current if item.id == first.id).actual_ended_at == datetime(
        2026, 9, 15, 9, 0, tzinfo=UTC
    )
    assert any(item.id == second.id for item in current)


def test_history_restores_deleted_session_and_child_deduction_separately(tmp_path: Path) -> None:
    now = datetime(2026, 9, 15, 18, 0, tzinfo=UTC)
    service, _, _ = build_service(tmp_path, now)
    session = service.add_manual_session(
        ManualWorkSessionCommand(
            datetime(2026, 9, 15, 7, 0, tzinfo=UTC),
            datetime(2026, 9, 15, 15, 0, tzinfo=UTC),
        )
    )
    deduction = service.add_manual_deduction(
        ManualDeductionCommand(
            session.id,
            DeductionKind.LUNCH,
            datetime(2026, 9, 15, 11, 0, tzinfo=UTC),
            datetime(2026, 9, 15, 11, 30, tzinfo=UTC),
        )
    )
    service.delete_work_session(session.id)
    history = service.entry_history_for_day(date(2026, 9, 15))
    session_version = next(entry for entry in history if entry.entity_type == "work_session")
    deduction_version = next(entry for entry in history if entry.entity_type == "deduction")

    service.restore_history_entry(session_version.audit_id)
    service.restore_history_entry(deduction_version.audit_id)

    assert len(service.completed_sessions_for_day(date(2026, 9, 15))) == 1
    assert [item.id for item in service.completed_deductions(session.id)] == [deduction.id]


@pytest.mark.parametrize("selected_history", [False, True])
def test_restore_open_lunch_requires_active_parent(tmp_path: Path, selected_history: bool) -> None:
    service, clock, database = build_service(tmp_path, datetime(2026, 9, 15, 8, 0, tzinfo=UTC))
    service.start_work(StartWorkCommand())
    clock.value += timedelta(minutes=30)
    service.start_deduction(StartDeductionCommand(DeductionKind.LUNCH))
    clock.value += timedelta(minutes=30)
    service.delete_work_session(SessionId("session-1"))
    history = service.entry_history_for_day(date(2026, 9, 15))
    work_version = next(item for item in history if item.entity_type == "work_session")
    lunch_version = next(item for item in history if item.entity_type == "deduction")
    service.restore_history_entry(work_version.audit_id)
    clock.value += timedelta(hours=1)
    service.finish_work(FinishWorkCommand())
    with SQLiteUnitOfWork(database) as uow:
        before = (
            uow.sessions.list_all(),
            uow.deductions.list_all(),
            uow.audit.list_active(clock.value),
        )

    with pytest.raises(DomainError):
        if selected_history:
            service.restore_history_entry(lunch_version.audit_id)
        else:
            service.restore_deduction(DeductionId("deduction-1"))

    with SQLiteUnitOfWork(database) as uow:
        after = (
            uow.sessions.list_all(),
            uow.deductions.list_all(),
            uow.audit.list_active(clock.value),
        )
    assert after == before
    clock.value += timedelta(hours=1)
    service.start_work(StartWorkCommand())
    clock.value += timedelta(minutes=30)
    assert (
        service.start_deduction(StartDeductionCommand(DeductionKind.LUNCH)).active_deduction_kind
        == DeductionKind.LUNCH
    )


@pytest.mark.parametrize("selected_history", [False, True])
def test_restore_open_work_rejects_completed_overlap(
    tmp_path: Path, selected_history: bool
) -> None:
    service, clock, database = build_service(tmp_path, datetime(2026, 9, 15, 8, 0, tzinfo=UTC))
    service.start_work(StartWorkCommand())
    clock.value += timedelta(minutes=30)
    service.delete_work_session(SessionId("session-1"))
    version = service.entry_history_for_day(date(2026, 9, 15))[0]
    clock.value = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
    service.add_manual_session(
        ManualWorkSessionCommand(
            datetime(2026, 9, 15, 9, 0, tzinfo=UTC),
            datetime(2026, 9, 15, 10, 0, tzinfo=UTC),
        )
    )
    with SQLiteUnitOfWork(database) as uow:
        before = (
            uow.sessions.list_all(),
            uow.deductions.list_all(),
            uow.audit.list_active(clock.value),
        )

    with pytest.raises(OverlappingIntervalError):
        if selected_history:
            service.restore_history_entry(version.audit_id)
        else:
            service.restore_work_session(SessionId("session-1"))

    with SQLiteUnitOfWork(database) as uow:
        after = (
            uow.sessions.list_all(),
            uow.deductions.list_all(),
            uow.audit.list_active(clock.value),
        )
    assert after == before
    assert service.active_state().session_id is None
    assert service.today_summary().net_seconds == 3600


@pytest.mark.parametrize("entity", ["work", "deduction"])
@pytest.mark.parametrize("action", ["edit", "latest", "history"])
def test_independent_legacy_aggregates_can_be_corrected_one_at_a_time(
    tmp_path: Path, entity: str, action: str
) -> None:
    service, clock, database = build_service(tmp_path, datetime(2026, 9, 16, 18, tzinfo=UTC))
    entries = []
    for day in (14, 15):
        start = datetime(2026, 9, day, 7 if entity == "work" else 8, tzinfo=UTC)
        finish = datetime(2026, 9, day, 16, tzinfo=UTC)
        session = service.add_manual_session(ManualWorkSessionCommand(start, finish))
        if entity == "work":
            service.update_work_session(
                UpdateWorkSessionCommand(session.id, start + timedelta(hours=1), finish)
            )
            entries.append((session.id, start, finish))
        else:
            deduction = service.add_manual_deduction(
                ManualDeductionCommand(
                    session.id,
                    DeductionKind.LUNCH,
                    start + timedelta(hours=4),
                    start + timedelta(hours=5),
                )
            )
            service.update_deduction(
                UpdateDeductionCommand(
                    deduction.id, start + timedelta(hours=5), start + timedelta(hours=6)
                )
            )
            entries.append((deduction.id, start + timedelta(hours=4), start + timedelta(hours=5)))
    if entity == "work":
        with SQLiteUnitOfWork(database) as uow:
            for identifier, start, _ in entries:
                # Historical sync accepted deductions outside their actual parent.
                uow.deductions.add(
                    Deduction(
                        DeductionId(f"imported-{identifier}"),
                        SessionId(identifier),
                        DeductionKind.LUNCH,
                        start,
                        start + timedelta(hours=1),
                        created_at=clock.value,
                        updated_at=clock.value,
                    )
                )
    else:
        with SQLiteUnitOfWork(database) as uow:
            for identifier, _, _ in entries:
                imported = uow.deductions.get(DeductionId(identifier))
                assert imported is not None
                imported.actual_started_at -= timedelta(hours=6)
                imported.actual_ended_at = imported.actual_started_at + timedelta(hours=1)
                uow.deductions.save(imported)

    for identifier, start, finish in entries:
        if action == "history":
            history = service.entry_history_for_day(start.date())
            version = next(item for item in history if item.entity_id == identifier)
            restored = service.restore_history_entry(version.audit_id)
        elif entity == "work":
            restored = (
                service.update_work_session(
                    UpdateWorkSessionCommand(SessionId(identifier), start, finish)
                )
                if action == "edit"
                else service.restore_work_session(SessionId(identifier))
            )
        else:
            restored = (
                service.update_deduction(
                    UpdateDeductionCommand(DeductionId(identifier), start, finish)
                )
                if action == "edit"
                else service.restore_deduction(DeductionId(identifier))
            )
        assert restored.actual_started_at == start
        assert restored.actual_ended_at == finish
    with SQLiteUnitOfWork(database) as uow:
        assert len(uow.audit.list_active(clock.value)) == 4


@pytest.mark.parametrize("entity", ["work_session", "deduction"])
@pytest.mark.parametrize("selected_history", [False, True])
def test_malformed_restored_rounding_is_refused_before_persistence(
    tmp_path: Path, entity: str, selected_history: bool
) -> None:
    service, clock, database = build_service(tmp_path, datetime(2026, 9, 15, 18, tzinfo=UTC))
    session = service.add_manual_session(
        ManualWorkSessionCommand(
            datetime(2026, 9, 15, 8, tzinfo=UTC), datetime(2026, 9, 15, 16, tzinfo=UTC)
        )
    )
    if entity == "work_session":
        identifier = str(session.id)
        service.update_work_session(
            UpdateWorkSessionCommand(
                session.id,
                datetime(2026, 9, 15, 9, tzinfo=UTC),
                datetime(2026, 9, 15, 16, tzinfo=UTC),
            )
        )
    else:
        deduction = service.add_manual_deduction(
            ManualDeductionCommand(
                session.id,
                DeductionKind.LUNCH,
                datetime(2026, 9, 15, 12, tzinfo=UTC),
                datetime(2026, 9, 15, 13, tzinfo=UTC),
            )
        )
        identifier = str(deduction.id)
        service.update_deduction(
            UpdateDeductionCommand(
                deduction.id,
                datetime(2026, 9, 15, 13, tzinfo=UTC),
                datetime(2026, 9, 15, 14, tzinfo=UTC),
            )
        )
    clock.value += timedelta(seconds=1)
    with SQLiteUnitOfWork(database) as uow:
        snapshot = uow.audit.latest(entity, identifier)
        assert snapshot is not None
        snapshot["rounding_minutes"] = 7
        uow.audit.record("invalid-rounding", entity, identifier, "update", snapshot, clock.value)
        before = (
            uow.sessions.list_all(),
            uow.deductions.list_all(),
            uow.audit.list_active(clock.value),
        )

    with pytest.raises(DomainError):
        if selected_history:
            service.restore_history_entry("invalid-rounding")
        elif entity == "work_session":
            service.restore_work_session(SessionId(identifier))
        else:
            service.restore_deduction(DeductionId(identifier))

    with SQLiteUnitOfWork(database) as uow:
        assert (
            uow.sessions.list_all(),
            uow.deductions.list_all(),
            uow.audit.list_active(clock.value),
        ) == before


def test_sleep_detection_clamps_to_session_and_avoids_double_deduction(tmp_path: Path) -> None:
    start = datetime(2026, 9, 15, 7, 0, tzinfo=UTC)
    service, clock, database = build_service(tmp_path, start)
    service.start_work(StartWorkCommand())
    clock.value = start + timedelta(minutes=30)
    assert (
        service.detect_sleep_gap(start - timedelta(minutes=20), start + timedelta(minutes=30))
        is None
    )
    clock.value = start + timedelta(minutes=31)
    gap = service.detect_sleep_gap(start - timedelta(minutes=20), clock.value)
    assert gap is not None
    assert gap.started_at == start
    service.resolve_sleep_gap("include")

    lunch_start = start + timedelta(hours=2)
    service.start_deduction(StartDeductionCommand(DeductionKind.LUNCH, lunch_start))
    service.finish_deduction(FinishDeductionCommand(lunch_start + timedelta(minutes=30)))
    clock.value = lunch_start + timedelta(hours=1, minutes=15)
    gap = service.detect_sleep_gap(lunch_start + timedelta(minutes=15), clock.value)
    assert gap is not None
    service.resolve_sleep_gap("exclude")

    with SQLiteUnitOfWork(database) as uow:
        deductions = uow.deductions.list_for_session(SessionId("session-1"))
    assert len(deductions) == 2
    lunch = next(item for item in deductions if item.kind is DeductionKind.LUNCH)
    sleep_break = next(item for item in deductions if item.kind is DeductionKind.SLEEP_BREAK)
    assert lunch.actual_ended_at == sleep_break.actual_started_at
    assert sleep_break.actual_ended_at == clock.value


def test_sleep_detection_can_be_disabled(tmp_path: Path) -> None:
    start = datetime(2026, 9, 15, 7, 0, tzinfo=UTC)
    service, clock, _ = build_service(tmp_path, start)
    service.start_work(StartWorkCommand())
    service.set_sleep_detection(False, 30)
    clock.value += timedelta(hours=1)

    assert service.detect_sleep_gap(start, clock.value) is None
    assert service.pending_sleep_gap() is None


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


def test_manual_lunch_can_be_added_while_work_session_is_active(tmp_path: Path) -> None:
    at = datetime(2026, 9, 15, 18, 0, tzinfo=UTC)
    service, clock, _ = build_service(tmp_path, at)
    session = service.start_work(StartWorkCommand()).session_id
    clock.value += timedelta(hours=2)

    deduction = service.add_manual_deduction(
        ManualDeductionCommand(
            session,
            DeductionKind.LUNCH,
            datetime(2026, 9, 15, 19, 0, tzinfo=UTC),
            datetime(2026, 9, 15, 19, 30, tzinfo=UTC),
        )
    )

    assert deduction.actual_started_at == datetime(2026, 9, 15, 19, 0, tzinfo=UTC)
    assert [entry.id for entry in service.completed_deductions(session)] == [deduction.id]
    assert service.active_state().session_id == session
    assert service.active_state().net_seconds == 90 * 60


def test_manual_lunch_can_be_corrected_while_work_session_is_active(tmp_path: Path) -> None:
    at = datetime(2026, 9, 15, 18, 0, tzinfo=UTC)
    service, clock, _ = build_service(tmp_path, at)
    session = service.start_work(StartWorkCommand()).session_id
    clock.value += timedelta(hours=2)
    deduction = service.add_manual_deduction(
        ManualDeductionCommand(
            session,
            DeductionKind.LUNCH,
            datetime(2026, 9, 15, 19, 0, tzinfo=UTC),
            datetime(2026, 9, 15, 19, 30, tzinfo=UTC),
        )
    )

    updated = service.update_deduction(
        UpdateDeductionCommand(
            deduction.id,
            datetime(2026, 9, 15, 19, 5, tzinfo=UTC),
            datetime(2026, 9, 15, 19, 25, tzinfo=UTC),
        )
    )

    assert updated.actual_started_at == datetime(2026, 9, 15, 19, 5, tzinfo=UTC)
    assert updated.actual_ended_at == datetime(2026, 9, 15, 19, 25, tzinfo=UTC)
    assert service.active_state().session_id == session
    assert service.active_state().net_seconds == 100 * 60


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


def test_cross_midnight_session_copies_office_status_and_next_day_is_editable(
    tmp_path: Path,
) -> None:
    start = datetime(2026, 9, 14, 20, 30, tzinfo=UTC)
    service, clock, _ = build_service(tmp_path, start)
    service.update_day_details(UpdateDayDetailsCommand(date(2026, 9, 14), WorkLocation.OFFICE, ""))
    service.start_work(StartWorkCommand())
    clock.value = datetime(2026, 9, 14, 23, 30, tzinfo=UTC)
    service.finish_work(FinishWorkCommand())

    summaries = {item.work_date: item for item in service.month(2026, 9)}
    assert summaries[date(2026, 9, 15)].location is WorkLocation.OFFICE
    service.update_day_details(
        UpdateDayDetailsCommand(date(2026, 9, 15), WorkLocation.REMOTE, "Working remotely")
    )
    summaries = {item.work_date: item for item in service.month(2026, 9)}
    assert summaries[date(2026, 9, 15)].location is WorkLocation.REMOTE


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


def test_history_entry_expires_after_30_days(tmp_path: Path) -> None:
    now = datetime(2026, 9, 15, 18, 0, tzinfo=UTC)
    service, clock, _ = build_service(tmp_path, now)
    session = service.add_manual_session(
        ManualWorkSessionCommand(
            datetime(2026, 9, 15, 7, 0, tzinfo=UTC),
            datetime(2026, 9, 15, 15, 0, tzinfo=UTC),
        )
    )
    service.delete_work_session(session.id)
    version = service.entry_history_for_day(date(2026, 9, 15))[0]
    clock.value += timedelta(days=30, seconds=1)

    assert service.entry_history_for_day(date(2026, 9, 15)) == []
    with pytest.raises(InvalidStateTransitionError):
        service.restore_history_entry(version.audit_id)


def test_long_sleep_requires_resolution_and_can_be_excluded_as_break(tmp_path: Path) -> None:
    start = datetime(2026, 9, 15, 7, 0, tzinfo=UTC)
    service, clock, database = build_service(tmp_path, start)
    service.start_work(StartWorkCommand())
    clock.value = start + timedelta(hours=2, minutes=31)
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


def test_month_and_weekly_totals_allocate_cross_midnight_work(tmp_path: Path) -> None:
    now = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
    service, _, _ = build_service(tmp_path, now)
    service.add_manual_session(
        ManualWorkSessionCommand(
            datetime(2026, 9, 15, 21, 30, tzinfo=UTC),
            datetime(2026, 9, 16, 1, 30, tzinfo=UTC),
        )
    )

    summaries = {summary.work_date: summary for summary in service.month(2026, 9)}
    assert len(summaries) == 30
    assert summaries[date(2026, 9, 15)].net_seconds == 30 * 60
    assert summaries[date(2026, 9, 16)].net_seconds == 3 * 60 * 60 + 30 * 60
    week = IsoWeek(2026, 38)
    progress = service.set_weekly_target(week, 37 * 60)
    assert progress.logged_seconds == 4 * 60 * 60
    assert progress.target_minutes == 37 * 60


def test_work_and_lunch_reminders_are_configurable_and_snoozable(tmp_path: Path) -> None:
    start = datetime(2026, 9, 15, 7, 0, tzinfo=UTC)
    service, clock, _ = build_service(tmp_path, start)
    assert service.reminder_settings() == ReminderSettingsView(True, 9 * 60, True, 45)
    service.set_reminder_settings(ReminderSettingsView(True, 60, True, 15))
    service.start_work(StartWorkCommand())
    clock.value += timedelta(hours=1)
    assert [reminder.kind for reminder in service.due_reminders()] == ["work"]
    assert service.due_reminders() == []
    service.snooze_reminder("work", 15)
    clock.value += timedelta(minutes=10)
    assert service.due_reminders() == []
    clock.value += timedelta(minutes=6)
    assert [reminder.kind for reminder in service.due_reminders()] == ["work"]

    service.start_deduction(StartDeductionCommand(DeductionKind.LUNCH))
    clock.value += timedelta(minutes=15)
    assert [reminder.kind for reminder in service.due_reminders()] == ["lunch"]


def test_second_lunch_in_same_work_gets_own_reminder(tmp_path: Path) -> None:
    start = datetime(2026, 9, 15, 7, 0, tzinfo=UTC)
    service, clock, _ = build_service(tmp_path, start)
    service.start_work(StartWorkCommand())
    service.start_deduction(StartDeductionCommand(DeductionKind.LUNCH))
    clock.value += timedelta(minutes=45)
    assert [item.kind for item in service.due_reminders()] == ["lunch"]
    service.snooze_reminder("lunch", 60)
    clock.value += timedelta(minutes=1)
    service.finish_deduction(FinishDeductionCommand())
    service.start_deduction(StartDeductionCommand(DeductionKind.LUNCH))
    clock.value += timedelta(minutes=45)

    assert [item.kind for item in service.due_reminders()] == ["lunch"]
    assert service.due_reminders() == []


@pytest.mark.parametrize("snooze_minutes", [15, 30, 60])
def test_current_lunch_snooze_uses_selected_duration(tmp_path: Path, snooze_minutes: int) -> None:
    start = datetime(2026, 9, 15, 7, 0, tzinfo=UTC)
    service, clock, _ = build_service(tmp_path, start)
    service.start_work(StartWorkCommand())
    service.start_deduction(StartDeductionCommand(DeductionKind.LUNCH))
    clock.value += timedelta(minutes=45)
    assert [item.kind for item in service.due_reminders()] == ["lunch"]
    service.snooze_reminder("lunch", snooze_minutes)
    clock.value += timedelta(minutes=snooze_minutes, seconds=-1)
    assert service.due_reminders() == []
    clock.value += timedelta(seconds=1)
    assert [item.kind for item in service.due_reminders()] == ["lunch"]


def test_ended_lunch_reminder_is_retired_and_undo_can_remind_again(tmp_path: Path) -> None:
    start = datetime(2026, 9, 15, 7, 0, tzinfo=UTC)
    service, clock, database = build_service(tmp_path, start)
    service.start_work(StartWorkCommand())
    clock.value += timedelta(hours=9)
    service.start_deduction(StartDeductionCommand(DeductionKind.LUNCH))
    assert [item.kind for item in service.due_reminders()] == ["work"]
    clock.value += timedelta(minutes=45)
    assert [item.kind for item in service.due_reminders()] == ["lunch"]
    service.finish_deduction(FinishDeductionCommand())
    assert service.due_reminders() == []
    assert service.undo_last_timer_action().active_deduction_kind is DeductionKind.LUNCH
    assert [item.kind for item in service.due_reminders()] == ["lunch"]

    restarted_ids = FixedIds()
    restarted_ids.deduction_count = 1
    restarted = TimeTrackingApplicationService(
        lambda: SQLiteUnitOfWork(database), clock, restarted_ids
    )
    assert restarted.due_reminders() == []
    clock.value += timedelta(minutes=1)
    restarted.finish_deduction(FinishDeductionCommand())
    restarted.start_deduction(StartDeductionCommand(DeductionKind.LUNCH))
    clock.value += timedelta(minutes=45)
    assert [item.kind for item in restarted.due_reminders()] == ["lunch"]


def test_deleting_active_lunch_does_not_suppress_next_lunch(tmp_path: Path) -> None:
    start = datetime(2026, 9, 15, 7, 0, tzinfo=UTC)
    service, clock, _ = build_service(tmp_path, start)
    service.start_work(StartWorkCommand())
    service.start_deduction(StartDeductionCommand(DeductionKind.LUNCH))
    clock.value += timedelta(minutes=45)
    assert [item.kind for item in service.due_reminders()] == ["lunch"]
    service.delete_deduction(DeductionId("deduction-1"))
    service.start_deduction(StartDeductionCommand(DeductionKind.LUNCH))
    clock.value += timedelta(minutes=45)
    assert [item.kind for item in service.due_reminders()] == ["lunch"]


def test_legacy_parent_keyed_lunch_state_is_ignored(tmp_path: Path) -> None:
    start = datetime(2026, 9, 15, 7, 0, tzinfo=UTC)
    service, clock, database = build_service(tmp_path, start)
    service.start_work(StartWorkCommand())
    service.start_deduction(StartDeductionCommand(DeductionKind.LUNCH))
    with SQLiteUnitOfWork(database) as uow:
        uow.settings.save("reminder_notified_lunch", {"session_id": "session-1"}, start)
        uow.settings.save(
            "reminder_snooze_lunch",
            {"session_id": "session-1", "until": (start + timedelta(hours=2)).isoformat()},
            start,
        )
    clock.value += timedelta(minutes=45)
    assert [item.kind for item in service.due_reminders()] == ["lunch"]
