"""Explicit conflict decisions are causal commands, validated and committed atomically."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from qi_flow.application.google_sync_service import SyncService
from qi_flow.application.sync_models import SyncChange, SyncReviewError, SyncTarget, finalize_group
from qi_flow.application.sync_payloads import session_payload
from qi_flow.application.sync_reconciliation import SyncReconciler
from qi_flow.domain.errors import OverlappingIntervalError
from qi_flow.domain.models import EntrySource, SessionId, WorkSession
from qi_flow.infrastructure.sqlite.database import SQLiteDatabase
from qi_flow.infrastructure.sqlite.repositories import SQLiteUnitOfWork
from qi_flow.infrastructure.system import UuidIdentifierGenerator

NOW = datetime(2026, 10, 3, 12, tzinfo=UTC)
TARGET = SyncTarget("sheet", "log")
KEY = ("work_session", "work")


class Clock:
    def now(self):
        return NOW


def work(finish=8, identifier="work"):
    return WorkSession(
        SessionId(identifier),
        datetime(2026, 10, 2, 7, tzinfo=UTC),
        datetime(2026, 10, 2, finish, tzinfo=UTC),
        source=EntrySource.MANUAL,
        created_at=NOW,
        updated_at=NOW,
    )


def change(identifier, finish=8, parents=(), entity_id="work", operation="upsert"):
    return finalize_group(
        (
            SyncChange(
                identifier,
                2,
                "work_session",
                entity_id,
                parents,
                "g-" + identifier,
                (),
                "",
                {},
                operation,
                session_payload(work(finish, entity_id)) if operation == "upsert" else None,
                NOW,
                "copied-device",
            ),
        )
    )[0]


@pytest.fixture
def rig(tmp_path):
    database = SQLiteDatabase(tmp_path / "resolution.sqlite3")
    database.initialize()

    def factory():
        return SQLiteUnitOfWork(database)

    class Gateway:
        def read_changes(self):
            raise AssertionError("Resolution must not need networking")

        def read_problems(self):
            return ()

        def append_changes(self, changes):
            raise AssertionError("Resolution only authors durable pending changes")

    with factory() as uow:
        uow.settings.save("google_sync_generation", 1, NOW)
        uow.settings.save("google_sync_enabled", True, NOW)
        uow.settings.save(
            "google_sync_v2_target", {"spreadsheet_id": "sheet", "log_id": "log"}, NOW
        )
        uow.sync_for(TARGET).set_state("migration_complete", True)
        uow.sync_for(TARGET).observe((change("base"),))
    reconciler = SyncReconciler(factory, TARGET, Clock(), UuidIdentifierGenerator(), generation=1)
    reconciler.reconcile()
    with factory() as uow:
        uow.sync_for(TARGET).observe((change("a", 9, ("base",)), change("b", 10, ("base",))))
    reconciler.reconcile()
    service = SyncService(
        factory, Gateway(), TARGET, Clock(), UuidIdentifierGenerator(), generation=1
    )
    return factory, service, reconciler


def review(factory, service):
    with factory() as uow:
        conflict = uow.sync_for(TARGET).conflicts()[0]
    return service.review(conflict.conflict_id)


def test_review_contains_local_competing_and_causal_base_versions(rig):
    factory, service, _ = rig
    reviewed = review(factory, service)
    assert reviewed.local_payloads[KEY]["actual_ended_at"] == work().actual_ended_at.isoformat()
    assert reviewed.conflict.head_ids == frozenset({"a", "b"})
    assert {c.change_id for c in reviewed.bases} == {"base"}
    with factory() as uow:
        assert uow.sync_for(TARGET).pending() == ()


def test_resolution_authors_one_complete_command_referencing_every_reviewed_head(rig):
    factory, service, reconciler = rig
    reviewed = review(factory, service)
    service.resolve(
        reviewed.conflict.conflict_id, reviewed.conflict.head_ids, {KEY: session_payload(work(9))}
    )
    with factory() as uow:
        repo = uow.sync_for(TARGET)
        assert not repo.conflicts()
        assert len(repo.pending()) == 1
        resolved = repo.pending()[0]
        assert set(resolved.parent_ids) == {"base", "a", "b"}
        assert repo.heads(KEY) == (resolved.change_id,)
        assert uow.sessions.get(SessionId("work")).actual_ended_at.hour == 9
    reconciler.reconcile()
    with factory() as uow:
        assert not uow.sync_for(TARGET).conflicts()


def test_new_remote_head_invalidates_open_review_without_losing_third_version(rig):
    factory, service, _ = rig
    reviewed = review(factory, service)
    with factory() as uow:
        uow.sync_for(TARGET).observe((change("third", 11, ("base",)),))
    with pytest.raises(SyncReviewError):
        service.resolve(
            reviewed.conflict.conflict_id,
            reviewed.conflict.head_ids,
            {KEY: session_payload(work(9))},
        )
    with factory() as uow:
        assert uow.sync_for(TARGET).conflicts()[0].head_ids == frozenset({"a", "b", "third"})
        assert uow.sync_for(TARGET).pending() == ()


def test_newer_local_payload_invalidates_review_even_if_provenance_was_restored(rig):
    factory, service, _ = rig
    reviewed = review(factory, service)
    with factory() as uow:
        uow.sessions.save(work(11))
    with pytest.raises(SyncReviewError, match="local"):
        service.resolve(
            reviewed.conflict.conflict_id,
            reviewed.conflict.head_ids,
            {KEY: session_payload(work(9))},
        )
    with factory() as uow:
        assert uow.sessions.get(SessionId("work")).actual_ended_at.hour == 11
        assert not uow.sync_for(TARGET).pending()


def test_edit_delete_choice_is_explicit_and_retains_all_ancestors(rig):
    factory, service, _ = rig
    with factory() as uow:
        uow.sync_for(TARGET).observe((change("deleted", parents=("base",), operation="delete"),))
    reviewed = review(factory, service)
    service.resolve(reviewed.conflict.conflict_id, reviewed.conflict.head_ids, {KEY: None})
    with factory() as uow:
        assert uow.sessions.get(SessionId("work")).deleted_at == NOW
        assert uow.sync_for(TARGET).pending()[0].operation == "delete"
        assert {"a", "b", "deleted"} <= set(uow.sync_for(TARGET).pending()[0].parent_ids)


def test_invalid_overlap_choice_rolls_back_command_and_keeps_conflict(rig):
    factory, service, _ = rig
    with factory() as uow:
        uow.sessions.add(
            replace(
                work(identifier="other"),
                actual_started_at=datetime(2026, 10, 2, 8, tzinfo=UTC),
                actual_ended_at=datetime(2026, 10, 2, 9, tzinfo=UTC),
            )
        )
    reviewed = review(factory, service)
    with pytest.raises(OverlappingIntervalError, match="overlap"):
        service.resolve(
            reviewed.conflict.conflict_id,
            reviewed.conflict.head_ids,
            {KEY: session_payload(work(9))},
        )
    with factory() as uow:
        assert uow.sessions.get(SessionId("work")).actual_ended_at.hour == 8
        assert uow.sync_for(TARGET).pending() == ()


def test_third_writer_observed_after_resolution_remains_a_new_conflict(rig):
    factory, service, reconciler = rig
    reviewed = review(factory, service)
    service.resolve(
        reviewed.conflict.conflict_id, reviewed.conflict.head_ids, {KEY: session_payload(work(9))}
    )
    with factory() as uow:
        resolved_id = uow.sync_for(TARGET).pending()[0].change_id
        uow.sync_for(TARGET).observe((change("third", 11, ("base",)),))
    reconciler.reconcile()
    with factory() as uow:
        assert uow.sync_for(TARGET).conflicts()[0].head_ids == frozenset({resolved_id, "third"})
        assert uow.sessions.get(SessionId("work")).actual_ended_at.hour == 9
        assert uow.sync_for(TARGET).conflicts()


def test_failed_outbox_insert_rolls_back_payload_and_conflict_closure(rig):
    import sqlite3

    factory, service, _ = rig
    reviewed = review(factory, service)
    with factory() as uow:
        uow._connection.execute(
            "CREATE TRIGGER reject_resolution BEFORE INSERT ON sync_changes "
            "BEGIN SELECT RAISE(ABORT, 'refused'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        service.resolve(
            reviewed.conflict.conflict_id,
            reviewed.conflict.head_ids,
            {KEY: session_payload(work(9))},
        )
    with factory() as uow:
        assert uow.sessions.get(SessionId("work")).actual_ended_at.hour == 8
        assert uow.sync_for(TARGET).conflicts()
        assert uow.sync_for(TARGET).pending() == ()


def test_resolution_cannot_attach_a_new_completed_child_to_local_active_work(rig):
    from datetime import timedelta

    from qi_flow.application.sync_payloads import deduction_payload
    from qi_flow.domain.models import Deduction, DeductionId, DeductionKind

    factory, service, reconciler = rig
    child = Deduction(
        DeductionId("child"),
        SessionId("work"),
        DeductionKind.LUNCH,
        work().actual_started_at + timedelta(minutes=15),
        work().actual_started_at + timedelta(minutes=30),
        created_at=NOW,
        updated_at=NOW,
    )
    remote = finalize_group(
        (
            SyncChange(
                "child-version",
                2,
                "deduction",
                "child",
                (),
                "g-child",
                (),
                "",
                {KEY: ("base",)},
                "upsert",
                deduction_payload(child),
                NOW,
                "device",
            ),
        )
    )[0]
    with factory() as uow:
        uow.sessions.add(
            WorkSession(
                SessionId("running"), NOW - timedelta(hours=2), created_at=NOW, updated_at=NOW
            )
        )
        uow.sync_for(TARGET).observe((remote,))
    reconciler.reconcile()
    with factory() as uow:
        conflict = next(
            c for c in uow.sync_for(TARGET).conflicts() if ("deduction", "child") in c.entity_keys
        )
    reviewed = service.review(conflict.conflict_id)
    proposed_child = replace(
        child,
        session_id=SessionId("running"),
        actual_started_at=NOW - timedelta(hours=1),
        actual_ended_at=NOW - timedelta(minutes=30),
    )
    choices = {
        KEY: session_payload(work()),
        ("deduction", "child"): deduction_payload(proposed_child),
    }
    with pytest.raises(ValueError, match=r"completed|running|active"):
        service.resolve(reviewed.conflict.conflict_id, reviewed.conflict.head_ids, choices)
    with factory() as uow:
        assert uow.sessions.get(SessionId("running")).is_active
        assert uow.deductions.get(DeductionId("child")) is None
        assert uow.sync_for(TARGET).pending() == ()
        assert uow.sync_for(TARGET).conflicts()
