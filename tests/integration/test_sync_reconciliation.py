"""Causal import decisions preserve competing history and valid local aggregates."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

import qi_flow.application.sync_reconciliation as reconciliation
from qi_flow.application.sync_models import SyncChange, SyncTarget, finalize_group
from qi_flow.application.sync_payloads import deduction_payload, session_payload
from qi_flow.domain.models import (
    Deduction,
    DeductionId,
    DeductionKind,
    EntrySource,
    SessionId,
    WorkSession,
)
from qi_flow.infrastructure.sqlite.database import SQLiteDatabase
from qi_flow.infrastructure.sqlite.repositories import SQLiteUnitOfWork
from qi_flow.infrastructure.system import UuidIdentifierGenerator

NOW = datetime(2026, 10, 3, 12, tzinfo=UTC)
TARGET = SyncTarget("sheet", "log")


class Clock:
    def now(self):
        return NOW


def session(identifier="work", start=7, finish=8, revision=1):
    return WorkSession(
        SessionId(identifier),
        datetime(2026, 10, 2, start, tzinfo=UTC),
        datetime(2026, 10, 2, finish, tzinfo=UTC),
        source=EntrySource.MANUAL,
        created_at=NOW,
        updated_at=NOW,
        rounding_minutes=1,
        revision=revision,
    )


def work_change(
    change_id, work=None, *, parents=(), group_id=None, bases=None, operation="upsert", payload=None
):
    work = work or session()
    data = session_payload(work) if operation == "upsert" and payload is None else payload
    change = SyncChange(
        change_id,
        2,
        "work_session",
        str(work.id),
        parents,
        group_id or "g-" + change_id,
        (),
        "",
        bases or {},
        operation,
        data,
        NOW,
        "same-copied-device",
    )
    return finalize_group((change,))[0]


def completed_group(*changes):
    group_id = changes[0].group_id
    return finalize_group(
        tuple(
            replace(c, group_id=group_id, aggregate_base_heads=changes[0].aggregate_base_heads)
            for c in changes
        )
    )


@pytest.fixture
def rig(tmp_path):
    database = SQLiteDatabase(tmp_path / "reconcile.sqlite3")
    database.initialize()

    def factory():
        return SQLiteUnitOfWork(database)

    def stage(*changes):
        with factory() as uow:
            uow.sync_for(TARGET).observe(changes)

    def reconcile():
        return reconciliation.SyncReconciler(
            factory, TARGET, Clock(), UuidIdentifierGenerator()
        ).reconcile()

    return factory, stage, reconcile


def test_unequal_revisions_and_copied_device_do_not_choose_between_independent_heads(rig):
    factory, stage, reconcile = rig
    seed = work_change("seed")
    stage(seed)
    reconcile()
    a = work_change("a", session(finish=9, revision=2), parents=("seed",))
    b = work_change("b", session(finish=10, revision=99), parents=("seed",))
    stage(a, b)
    reconcile()
    with factory() as uow:
        repo = uow.sync_for(TARGET)
        assert len(repo.conflicts()) == 1
        assert repo.conflicts()[0].head_ids == frozenset({"a", "b"})
        assert uow.sessions.get(SessionId("work")).actual_ended_at.hour == 8
        assert {c.change_id for c in repo.observed()} == {"seed", "a", "b"}
        assert repo.pending() == ()


def test_different_ids_for_same_span_do_not_double_the_timesheet(rig):
    factory, stage, reconcile = rig
    stage(work_change("first", session("first")))
    reconcile()
    stage(work_change("second", session("second")))
    reconcile()
    with factory() as uow:
        live = [s for s in uow.sessions.list_all() if s.deleted_at is None]
        assert len(live) == 1
        assert int((live[0].actual_ended_at - live[0].actual_started_at).total_seconds()) == 3600
        assert uow.sync_for(TARGET).conflicts()
        assert len(uow.sync_for(TARGET).observed()) == 2


def test_missing_out_of_order_ancestry_waits_then_fast_forwards_without_outbox_echo(rig):
    factory, stage, reconcile = rig
    latest = work_change("latest", session(finish=9), parents=("seed",))
    stage(latest)
    reconcile()
    with factory() as uow:
        assert uow.sessions.list_all() == []
        assert uow.sync_for(TARGET).get_state("staged_reconciliation")
    stage(work_change("seed"))
    reconcile()
    reconcile()
    with factory() as uow:
        assert uow.sessions.get(SessionId("work")).actual_ended_at.hour == 9
        assert uow.sync_for(TARGET).heads(("work_session", "work")) == ("latest",)
        assert uow.sync_for(TARGET).pending() == ()
        assert not uow.sync_for(TARGET).get_state("staged_reconciliation")


@pytest.mark.parametrize("bad", ["open", "future", "effective", "missing_source"])
def test_invalid_v2_payload_is_preserved_while_unrelated_group_materializes(rig, bad):
    factory, stage, reconcile = rig
    payload = session_payload(session("bad"))
    if bad == "open":
        payload["actual_ended_at"] = None
    elif bad == "future":
        payload["actual_started_at"] = (NOW + timedelta(hours=1)).isoformat()
        payload["actual_ended_at"] = (NOW + timedelta(hours=2)).isoformat()
    elif bad == "effective":
        payload["effective_started_at"] = NOW.isoformat()
    else:
        del payload["source"]
    stage(
        work_change("bad", session("bad"), payload=payload),
        work_change("good", session("good", start=10, finish=11)),
    )
    reconcile()
    with factory() as uow:
        assert [str(s.id) for s in uow.sessions.list_all()] == ["good"]
        assert uow.sync_for(TARGET).conflicts()
        assert len(uow.sync_for(TARGET).observed()) == 2


def test_parent_and_overlapping_children_group_cannot_materialize_in_parts(rig):
    factory, stage, reconcile = rig
    parent = work_change("parent", session(finish=12))
    children = []
    for identifier, left, right in [("one", 9, 10), ("two", 9, 11)]:
        deduction = Deduction(
            DeductionId(identifier),
            SessionId("work"),
            DeductionKind.LUNCH,
            datetime(2026, 10, 2, left, tzinfo=UTC),
            datetime(2026, 10, 2, right, tzinfo=UTC),
            rounding_minutes=1,
            created_at=NOW,
            updated_at=NOW,
        )
        children.append(
            SyncChange(
                identifier,
                2,
                "deduction",
                identifier,
                (),
                parent.group_id,
                (),
                "",
                {},
                "upsert",
                deduction_payload(deduction),
                NOW,
                "device",
            )
        )
    stage(*completed_group(parent, *children))
    reconcile()
    with factory() as uow:
        assert uow.sessions.list_all() == [] and uow.deductions.list_all() == []
        assert uow.sync_for(TARGET).conflicts()
        assert len(uow.sync_for(TARGET).observed()) == 3


def test_remote_tombstone_descends_obsolete_head_and_prevents_backup_resurrection(rig):
    factory, stage, reconcile = rig
    stage(work_change("seed"))
    reconcile()
    stage(work_change("deleted", parents=("seed",), operation="delete"))
    reconcile()
    reconcile()
    with factory() as uow:
        assert uow.sessions.get(SessionId("work")).deleted_at is not None
        assert uow.sync_for(TARGET).heads(("work_session", "work")) == ("deleted",)
        assert uow.sync_for(TARGET).pending() == ()


def test_delete_versus_edit_stays_a_conflict(rig):
    factory, stage, reconcile = rig
    stage(work_change("seed"))
    reconcile()
    stage(
        work_change("delete", parents=("seed",), operation="delete"),
        work_change("edit", session(finish=9, revision=999), parents=("seed",)),
    )
    reconcile()
    with factory() as uow:
        assert uow.sessions.get(SessionId("work")).deleted_at is None
        assert uow.sync_for(TARGET).conflicts()[0].head_ids == frozenset({"delete", "edit"})


def test_valid_parent_and_child_commit_materializes_once_and_survives_restart(rig):
    factory, stage, reconcile = rig
    parent = work_change("parent", session(finish=12))
    deduction = Deduction(
        DeductionId("child"),
        SessionId("work"),
        DeductionKind.LUNCH,
        datetime(2026, 10, 2, 9, tzinfo=UTC),
        datetime(2026, 10, 2, 10, tzinfo=UTC),
        rounding_minutes=1,
        created_at=NOW,
        updated_at=NOW,
    )
    child = SyncChange(
        "child",
        2,
        "deduction",
        "child",
        (),
        parent.group_id,
        (),
        "",
        {},
        "upsert",
        deduction_payload(deduction),
        NOW,
        "device",
    )
    stage(*completed_group(parent, child))
    reconcile()
    reconcile()
    with factory() as uow:
        assert len(uow.sessions.list_all()) == len(uow.deductions.list_all()) == 1
        assert uow.sync_for(TARGET).pending() == ()
        assert uow.sync_for(TARGET).conflicts() == ()


def test_concurrent_parent_and_child_edits_require_aggregate_review(rig):
    factory, stage, reconcile = rig
    parent = work_change("parent", session(finish=12))
    deduction = Deduction(
        DeductionId("child"),
        SessionId("work"),
        DeductionKind.LUNCH,
        datetime(2026, 10, 2, 9, tzinfo=UTC),
        datetime(2026, 10, 2, 10, tzinfo=UTC),
        rounding_minutes=1,
        created_at=NOW,
        updated_at=NOW,
    )
    child = SyncChange(
        "child",
        2,
        "deduction",
        "child",
        (),
        parent.group_id,
        (),
        "",
        {},
        "upsert",
        deduction_payload(deduction),
        NOW,
        "device",
    )
    stage(*completed_group(parent, child))
    reconcile()
    bases = {("work_session", "work"): ("parent",), ("deduction", "child"): ("child",)}
    parent_edit = work_change("parent-edit", session(finish=11), parents=("parent",), bases=bases)
    changed = deduction_payload(
        replace(deduction, actual_ended_at=datetime(2026, 10, 2, 10, 30, tzinfo=UTC))
    )
    child_edit = finalize_group(
        (
            replace(
                child,
                change_id="child-edit",
                parent_ids=("child",),
                group_id="g-child-edit",
                aggregate_base_heads=bases,
                payload=changed,
            ),
        )
    )[0]
    stage(parent_edit, child_edit)
    reconcile()
    with factory() as uow:
        assert uow.sessions.get(SessionId("work")).actual_ended_at.hour == 12
        assert uow.deductions.get(DeductionId("child")).actual_ended_at.minute == 0
        assert uow.sync_for(TARGET).conflicts()[0].head_ids == frozenset(
            {"parent-edit", "child-edit"}
        )


def test_differing_duplicate_id_never_materializes_the_first_arbitrary_variant(rig):
    factory, stage, reconcile = rig
    stage(work_change("same"), work_change("same", session(finish=9)))
    reconcile()
    with factory() as uow:
        assert uow.sessions.list_all() == []
        assert uow.sync_for(TARGET).problems()


def test_composed_sync_imports_before_any_publication_and_records_verified_success(rig):
    import qi_flow.application.google_sync_service as services

    factory, _, _ = rig

    class Gateway:
        def read_changes(self):
            return (work_change("seed"),)

        def read_problems(self):
            return ()

        def append_changes(self, changes):
            raise AssertionError("Remote import must not author outbox echo.")

    with factory() as uow:
        uow.settings.save("google_sync_generation", 1, NOW)
        uow.settings.save("google_sync_enabled", True, NOW)
        uow.settings.save(
            "google_sync_v2_target", {"spreadsheet_id": "sheet", "log_id": "log"}, NOW
        )
        uow.sync_for(TARGET).set_state("migration_complete", True)
    service = services.SyncService(
        factory, Gateway(), TARGET, Clock(), UuidIdentifierGenerator(), generation=1
    )
    result = service.run_once(cancelled=lambda: False, deadline=NOW + timedelta(minutes=1))
    assert result.state == "synced"
    assert result.last_success == NOW
    assert result.pending_count == result.conflict_count == 0
    with factory() as uow:
        assert uow.sessions.get(SessionId("work")).actual_ended_at.hour == 8
        assert uow.sync_for(TARGET).pending() == ()


def test_day_details_fork_and_review_guard_survive_reopen(rig):
    factory, stage, reconcile = rig
    seed = finalize_group(
        (
            SyncChange(
                "day-seed",
                2,
                "day_details",
                "2026-10-02",
                (),
                "g-day",
                (),
                "",
                {},
                "upsert",
                {"location": "office", "note": "original", "revision": 1},
                NOW,
                "device",
            ),
        )
    )[0]
    stage(seed)
    reconcile()
    a = finalize_group(
        (
            replace(
                seed,
                change_id="day-a",
                group_id="g-a",
                parent_ids=("day-seed",),
                payload={"location": "office", "note": "first", "revision": 2},
            ),
        )
    )[0]
    b = finalize_group(
        (
            replace(
                seed,
                change_id="day-b",
                group_id="g-b",
                parent_ids=("day-seed",),
                payload={"location": "remote", "note": "second", "revision": 2},
            ),
        )
    )[0]
    stage(a, b)
    reconcile()
    with factory() as uow:
        first = uow.sync_for(TARGET).conflicts()[0]
        assert uow.days.get(date(2026, 10, 2)).note == "original"
        assert first.head_ids == frozenset({"day-a", "day-b"})
    reconcile()
    with factory() as uow:
        assert uow.sync_for(TARGET).conflicts() == (first,)
        assert uow.sync_for(TARGET).pending() == ()


def test_persistence_refusal_rolls_back_import_but_keeps_prior_observations(rig):
    import sqlite3

    factory, stage, reconcile = rig
    change = work_change("observed")
    stage(change)
    with factory() as uow:
        uow._connection.execute(
            "CREATE TRIGGER refuse_import BEFORE INSERT ON work_sessions "
            "BEGIN SELECT RAISE(ABORT, 'refused import'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        reconcile()
    with factory() as uow:
        assert uow.sessions.list_all() == []
        assert uow.sync_for(TARGET).observed() == (change,)
        assert uow.sync_for(TARGET).heads(change.entity_key) == ()
        assert uow.sync_for(TARGET).pending() == ()


def test_child_import_cannot_attach_to_a_local_running_parent(rig):
    factory, stage, reconcile = rig
    with factory() as uow:
        uow.sessions.add(
            WorkSession(SessionId("work"), NOW - timedelta(hours=2), created_at=NOW, updated_at=NOW)
        )
    child = Deduction(
        DeductionId("child"),
        SessionId("work"),
        DeductionKind.LUNCH,
        NOW - timedelta(hours=1),
        NOW - timedelta(minutes=30),
        created_at=NOW,
        updated_at=NOW,
        rounding_minutes=1,
    )
    change = finalize_group(
        (
            SyncChange(
                "child",
                2,
                "deduction",
                "child",
                (),
                "g-child",
                (),
                "",
                {},
                "upsert",
                deduction_payload(child),
                NOW,
                "device",
            ),
        )
    )[0]
    stage(change)
    reconcile()
    with factory() as uow:
        assert uow.sessions.get_active().id == SessionId("work")
        assert uow.deductions.list_all() == []
        assert uow.sync_for(TARGET).conflicts()


def test_incomplete_group_preserves_members_without_partial_materialization(rig):
    factory, stage, reconcile = rig
    complete = completed_group(
        work_change("one", session("one")), work_change("two", session("two", start=9, finish=10))
    )
    stage(complete[0])
    reconcile()
    with factory() as uow:
        assert uow.sessions.list_all() == []
        assert uow.sync_for(TARGET).observed() == complete[:1]
    stage(complete[1])
    reconcile()
    with factory() as uow:
        assert len(uow.sessions.list_all()) == 2
        assert not uow.sync_for(TARGET).get_state("staged_reconciliation")


def test_malformed_day_tombstone_is_refused_before_any_group_writes(rig):
    factory, stage, reconcile = rig
    change = work_change("work")
    malformed = SyncChange(
        "day",
        2,
        "day_details",
        "invalid-date",
        (),
        change.group_id,
        (),
        "",
        {},
        "delete",
        None,
        NOW,
        "device",
    )
    stage(*completed_group(change, malformed))
    reconcile()
    with factory() as uow:
        assert uow.sessions.list_all() == []
        assert uow.sync_for(TARGET).conflicts()


def test_descendant_of_ambiguous_id_cannot_claim_proven_ancestry(rig):
    factory, stage, reconcile = rig
    stage(work_change("seed"), work_change("seed", session(finish=9)))
    stage(work_change("descendant", session(finish=10), parents=("seed",)))
    reconcile()
    with factory() as uow:
        assert uow.sessions.list_all() == []
        assert uow.sync_for(TARGET).problems()


def test_restored_pending_edit_is_reconciled_against_remote_delete_before_append(rig):
    from qi_flow.application.dto import UpdateWorkSessionCommand
    from qi_flow.application.google_sync_service import SyncService
    from qi_flow.application.time_tracking import TimeTrackingApplicationService

    factory, stage, reconcile = rig
    seed = work_change("seed")
    stage(seed)
    reconcile()
    with factory() as uow:
        uow.settings.save("google_sync_generation", 1, NOW)
        uow.settings.save("google_sync_enabled", True, NOW)
        uow.settings.save(
            "google_sync_v2_target", {"spreadsheet_id": "sheet", "log_id": "log"}, NOW
        )
        uow.sync_for(TARGET).set_state("migration_complete", True)
    tracking = TimeTrackingApplicationService(factory, Clock(), UuidIdentifierGenerator())
    tracking.update_work_session(
        UpdateWorkSessionCommand(
            SessionId("work"),
            datetime(2026, 10, 2, 7, tzinfo=UTC),
            datetime(2026, 10, 2, 9, tzinfo=UTC),
        )
    )
    with factory() as uow:
        pending = uow.sync_for(TARGET).pending()

    class Gateway:
        def read_changes(self):
            return seed, work_change("delete", parents=("seed",), operation="delete")

        def read_problems(self):
            return ()

        def append_changes(self, changes):
            raise AssertionError("A restored divergent edit must not publish before review.")

    result = SyncService(
        factory, Gateway(), TARGET, Clock(), UuidIdentifierGenerator(), generation=1
    ).run_once(cancelled=lambda: False, deadline=NOW + timedelta(minutes=1))
    assert result.state == "conflict" and result.last_success is None
    with factory() as uow:
        assert uow.sync_for(TARGET).pending() == pending
        assert uow.sync_for(TARGET).conflicts()[0].head_ids == frozenset(
            {"delete", pending[0].change_id}
        )
        assert uow.sessions.get(SessionId("work")).actual_ended_at.hour == 9


def test_protocol_tombstone_survives_recovery_audit_expiry(rig):
    factory, stage, reconcile = rig
    stage(work_change("seed"))
    reconcile()
    with factory() as uow:
        uow.audit.record("history", "work_session", "work", "update", {}, NOW)
    stage(work_change("delete", parents=("seed",), operation="delete"))

    class LaterClock:
        def now(self):
            return NOW + timedelta(days=40)

    reconciliation.SyncReconciler(
        factory, TARGET, LaterClock(), UuidIdentifierGenerator()
    ).reconcile()
    with factory() as uow:
        assert uow.audit.list_active(NOW + timedelta(days=40)) == []
        assert len(uow.sync_for(TARGET).observed()) == 2
        assert uow.sessions.get(SessionId("work")).deleted_at is not None
