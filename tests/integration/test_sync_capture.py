"""Commands and causal publication history commit as one local transaction."""

import sqlite3
from datetime import UTC, date, datetime, timedelta

import pytest

from qi_flow.application.dto import (
    FinishDeductionCommand,
    FinishWorkCommand,
    ManualDeductionCommand,
    ManualWorkSessionCommand,
    StartDeductionCommand,
    StartWorkCommand,
    UpdateDayDetailsCommand,
    UpdateWorkSessionCommand,
)
from qi_flow.application.google_sync import GoogleSyncSettings
from qi_flow.application.sync_models import SyncTarget, validate_group
from qi_flow.application.testhuset import TesthusetService
from qi_flow.application.time_tracking import TimeTrackingApplicationService
from qi_flow.domain.errors import InvalidStateTransitionError
from qi_flow.domain.models import DeductionKind, WorkLocation
from qi_flow.domain.testhuset import ProjectTask
from qi_flow.infrastructure.sqlite.database import SQLiteDatabase
from qi_flow.infrastructure.sqlite.repositories import SQLiteUnitOfWork
from qi_flow.infrastructure.system import UuidIdentifierGenerator
from qi_flow.infrastructure.testhuset_cache import JsonTaskCache

TARGET = SyncTarget("sheet-a", "log-a")


class Clock:
    def __init__(self):
        self.value = datetime(2026, 10, 3, 12, tzinfo=UTC)

    def now(self):
        return self.value


@pytest.fixture
def rig(tmp_path):
    database = SQLiteDatabase(tmp_path / "capture.sqlite3")
    database.initialize()
    clock = Clock()
    ids = UuidIdentifierGenerator()

    def factory():
        return SQLiteUnitOfWork(database)

    settings = GoogleSyncSettings(factory, clock)
    settings.save_values(
        "https://docs.google.com/spreadsheets/d/sheet-a/edit", "client.apps.googleusercontent.com"
    )
    with factory() as uow:
        uow.settings.save("google_sync_enabled", True, clock.now())
        uow.settings.save(
            "google_sync_v2_target", {"spreadsheet_id": "sheet-a", "log_id": "log-a"}, clock.now()
        )
        uow.sync_for(TARGET).set_state("migration_complete", True)
    service = TimeTrackingApplicationService(factory, clock, ids)
    return service, clock, database, factory, settings


def manual(service):
    return service.add_manual_session(
        ManualWorkSessionCommand(
            datetime(2026, 10, 2, 7, tzinfo=UTC), datetime(2026, 10, 2, 14, tzinfo=UTC)
        )
    )


def test_only_a_committed_finish_notifies_the_sync_trigger(rig):
    _, clock, database, factory, _ = rig
    notifications = []

    def finished():
        with factory() as uow:
            assert uow.sessions.get_active() is None
            assert uow.sync_for(TARGET).pending()
        notifications.append("finished")

    service = TimeTrackingApplicationService(
        factory, clock, UuidIdentifierGenerator(), on_work_finished=finished
    )
    service.start_work(StartWorkCommand())
    assert notifications == []
    clock.value += timedelta(minutes=10)
    service.start_deduction(StartDeductionCommand(DeductionKind.LUNCH))
    with pytest.raises(InvalidStateTransitionError, match="End lunch"):
        service.finish_work(FinishWorkCommand())
    assert notifications == []
    clock.value += timedelta(minutes=20)
    service.finish_deduction(FinishDeductionCommand())
    assert notifications == []

    with database.transaction() as connection:
        connection.execute(
            "CREATE TRIGGER fail_finish BEFORE UPDATE ON work_sessions "
            "BEGIN SELECT RAISE(ABORT, 'finish failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="finish failure"):
        service.finish_work(FinishWorkCommand())
    assert notifications == []
    with database.transaction() as connection:
        connection.execute("DROP TRIGGER fail_finish")

    service.finish_work(FinishWorkCommand())
    assert notifications == ["finished"]


def test_successive_offline_edits_retain_causal_parents_after_restart(rig):
    service, _, database, _, _ = rig
    session = manual(service)
    for hour in (13, 12):
        service.update_work_session(
            UpdateWorkSessionCommand(
                session.id, session.actual_started_at, datetime(2026, 10, 2, hour, tzinfo=UTC)
            )
        )
    with SQLiteUnitOfWork(SQLiteDatabase(database.database_file)) as uow:
        repo = uow.sync_for(TARGET)
        changes = repo.pending()
        assert len(changes) == 3
        assert changes[0].parent_ids == ()
        assert changes[1].parent_ids == (changes[0].change_id,)
        assert changes[2].parent_ids == (changes[1].change_id,)
        assert repo.heads(changes[0].entity_key) == (changes[2].change_id,)
        for change in changes:
            validate_group((change,))


@pytest.mark.parametrize("table", ["work_sessions", "sync_outbox"])
def test_command_failure_rolls_back_record_audit_and_outbox(rig, table):
    service, _, database, factory, _ = rig
    session = manual(service)
    with factory() as uow:
        before = uow.sync_for(TARGET).pending()
        audit = uow.audit.list_active(datetime(2026, 10, 3, 12, tzinfo=UTC))
    event = "UPDATE" if table == "work_sessions" else "INSERT"
    with database.transaction() as connection:
        connection.execute(
            f"CREATE TRIGGER fail_capture BEFORE {event} ON {table} "
            "BEGIN SELECT RAISE(ABORT, 'capture failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="capture failure"):
        service.update_work_session(
            UpdateWorkSessionCommand(
                session.id, session.actual_started_at, datetime(2026, 10, 2, 13, tzinfo=UTC)
            )
        )
    assert service.completed_sessions() == [session]
    with factory() as uow:
        assert uow.sync_for(TARGET).pending() == before
        assert uow.audit.list_active(datetime(2026, 10, 3, 12, tzinfo=UTC)) == audit


def test_finish_grace_undo_and_repeat_completion_preserve_uncertain_ancestry(rig):
    service, clock, _, factory, _ = rig
    service.start_work(StartWorkCommand(clock.now() - timedelta(hours=4)))
    service.start_deduction(StartDeductionCommand(DeductionKind.LUNCH))
    clock.value += timedelta(minutes=30)
    service.finish_deduction(FinishDeductionCommand())
    with factory() as uow:
        assert uow.sync_for(TARGET).pending() == ()
    service.finish_work(FinishWorkCommand())
    with factory() as uow:
        repo = uow.sync_for(TARGET)
        completion = repo.pending()
        assert {change.entity_kind for change in completion} == {"work_session", "deduction"}
        validate_group(completion)
        for change in completion:
            assert repo.publication(change.change_id).not_before == clock.now() + timedelta(
                seconds=30
            )
        # Model accepted append whose response is lost; attempted state is durable.
        repo.mark_attempted(tuple(change.change_id for change in completion))
    service.undo_last_timer_action()
    with factory() as uow:
        changes = uow.sync_for(TARGET).pending()
        withdrawals = changes[len(completion) :]
        assert len(withdrawals) == 2
        assert all(change.operation == "withdraw" for change in withdrawals)
        for change in withdrawals:
            ancestor = next(c for c in completion if c.entity_key == change.entity_key)
            assert change.parent_ids == (ancestor.change_id,)
            assert uow.sync_for(TARGET).publication(ancestor.change_id).attempted
    clock.value += timedelta(seconds=5)
    service.finish_work(FinishWorkCommand())
    with factory() as uow:
        latest = uow.sync_for(TARGET).pending()[4:]
        assert len(latest) == 2
        for change in latest:
            assert change.parent_ids == (
                next(c for c in withdrawals if c.entity_key == change.entity_key).change_id,
            )


def test_day_details_are_captured_but_local_operational_settings_are_not(rig):
    service, _, _, factory, _ = rig
    service.update_day_details(
        UpdateDayDetailsCommand(date(2026, 10, 2), WorkLocation.OFFICE, "private note")
    )
    service.set_rounding_minutes(15)
    with factory() as uow:
        changes = uow.sync_for(TARGET).pending()
        assert len(changes) == 1
        assert changes[0].entity_key == ("day_details", "2026-10-02")
        assert changes[0].payload["note"] == "private note"


def test_unreviewed_binding_never_authors_changes(rig):
    service, _, _, factory, _ = rig
    with factory() as uow:
        uow.sync_for(TARGET).set_state("migration_complete", False)
    manual(service)
    with factory() as uow:
        assert uow.sync_for(TARGET).pending() == ()


def test_configuration_change_and_disable_keep_old_target_outbox_isolated(rig):
    service, _, _, factory, settings = rig
    session = manual(service)
    initial_generation = settings.generation()
    settings.save_values(
        "https://docs.google.com/spreadsheets/d/sheet-b/edit", "client.apps.googleusercontent.com"
    )
    assert settings.generation() > initial_generation
    assert settings.active_target() is None
    service.update_work_session(
        UpdateWorkSessionCommand(
            session.id, session.actual_started_at, datetime(2026, 10, 2, 13, tzinfo=UTC)
        )
    )
    settings.disable()
    with factory() as uow:
        assert len(uow.sync_for(TARGET).pending()) == 1
        assert uow.sync_for(SyncTarget("sheet-b", "log-b")).pending() == ()


def test_deduction_assignment_deletion_and_history_restore_capture_complete_aggregate(
    rig, tmp_path
):
    service, clock, _, factory, _ = rig
    session = manual(service)
    deduction = service.add_manual_deduction(
        ManualDeductionCommand(
            session.id,
            DeductionKind.LUNCH,
            datetime(2026, 10, 2, 10, tzinfo=UTC),
            datetime(2026, 10, 2, 10, 30, tzinfo=UTC),
        )
    )
    cache = JsonTaskCache(tmp_path / "tasks.json")
    cache.replace((ProjectTask("11-22", "project", "activity"),))
    assignments = TesthusetService(factory, clock, UuidIdentifierGenerator(), cache)
    assignments.assign(session.id, "11-22")
    service.delete_deduction(deduction.id)
    service.restore_deduction(deduction.id)
    with factory() as uow:
        groups = {}
        for change in uow.sync_for(TARGET).pending():
            groups.setdefault(change.group_id, []).append(change)
        assert len(groups) == 5
        for group in groups.values():
            validate_group(group)
        addition, assignment, deletion, restoration = list(groups.values())[1:]
        for group in (addition, assignment, deletion, restoration):
            assert {c.entity_kind for c in group} == {"work_session", "deduction"}
        assert (
            next(c for c in assignment if c.entity_kind == "work_session").payload[
                "testhuset_task_id"
            ]
            == "11-22"
        )
        tombstone = next(c for c in deletion if c.entity_kind == "deduction")
        restored = next(c for c in restoration if c.entity_kind == "deduction")
        assert tombstone.operation == "delete" and tombstone.payload is None
        assert restored.operation == "upsert" and restored.parent_ids == (tombstone.change_id,)


def test_parent_deletion_and_recovery_restore_keep_child_tombstone(rig):
    service, _, _, factory, _ = rig
    session = manual(service)
    service.add_manual_deduction(
        ManualDeductionCommand(
            session.id,
            DeductionKind.LUNCH,
            datetime(2026, 10, 2, 10, tzinfo=UTC),
            datetime(2026, 10, 2, 10, 30, tzinfo=UTC),
        )
    )
    service.delete_work_session(session.id)
    history = next(
        entry
        for entry in service.entry_history_for_day(date(2026, 10, 2))
        if entry.entity_id == str(session.id) and entry.action == "delete"
    )
    service.restore_history_entry(history.audit_id)
    with factory() as uow:
        changes = uow.sync_for(TARGET).pending()
        assert len(changes) == 7
        assert all(c.operation == "delete" for c in changes[3:5])
        assert {c.entity_kind: c.operation for c in changes[5:]} == {
            "work_session": "upsert",
            "deduction": "delete",
        }
        assert uow.sync_for(TARGET).heads(("work_session", str(session.id)))


def test_grace_and_materialized_heads_survive_service_and_database_restart(rig):
    service, clock, database, factory, _ = rig
    service.start_work(StartWorkCommand(clock.now() - timedelta(hours=1)))
    service.finish_work(FinishWorkCommand())
    deadline = clock.now() + timedelta(seconds=30)
    with SQLiteUnitOfWork(SQLiteDatabase(database.database_file)) as uow:
        repo = uow.sync_for(TARGET)
        first = repo.pending()[0]
        assert repo.publication(first.change_id).not_before == deadline
    clock.value += timedelta(seconds=5)
    restarted = TimeTrackingApplicationService(factory, clock, UuidIdentifierGenerator())
    session = restarted.completed_sessions()[0]
    restarted.update_work_session(
        UpdateWorkSessionCommand(
            session.id,
            session.actual_started_at + timedelta(minutes=5),
            session.actual_ended_at,
        )
    )
    with factory() as uow:
        repo = uow.sync_for(TARGET)
        latest = repo.pending()[-1]
        assert latest.parent_ids == (first.change_id,)
        assert repo.publication(latest.change_id).not_before == deadline


def test_same_configuration_preserves_generation_and_opt_out_blocks_capture(rig):
    service, _, _, factory, settings = rig
    generation = settings.generation()
    settings.save_values(
        "https://docs.google.com/spreadsheets/d/sheet-a/edit", "client.apps.googleusercontent.com"
    )
    assert settings.generation() == generation
    assert settings.active_target() == TARGET
    settings.disable()
    manual(service)
    assert settings.generation() > generation
    assert settings.active_target() is None
    with factory() as uow:
        assert uow.sync_for(TARGET).pending() == ()
