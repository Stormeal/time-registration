"""Reviewed cutover preserves each participant's history without revision winners."""

import importlib
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from qi_flow.application.google_sync import GoogleSyncConfiguration, GoogleSyncSettings
from qi_flow.application.sync_capture import active_target
from qi_flow.application.sync_models import canonical_json
from qi_flow.domain.models import EntrySource, SessionId, WorkSession
from qi_flow.infrastructure.sqlite.database import SQLiteDatabase
from qi_flow.infrastructure.sqlite.repositories import SQLiteUnitOfWork
from qi_flow.infrastructure.system import UuidIdentifierGenerator

NOW = datetime(2026, 10, 3, 12, tzinfo=UTC)
HEADER = ["kind", "id", "revision", "updated_at_utc", "payload_json"]
CONFIG = GoogleSyncConfiguration(
    "https://docs.google.com/spreadsheets/d/sheet/edit", "test.apps.googleusercontent.com"
)


class Clock:
    def now(self):
        return NOW


def work(finish=8, revision=1):
    return WorkSession(
        SessionId("work"),
        datetime(2026, 10, 2, 7, tzinfo=UTC),
        datetime(2026, 10, 2, finish, tzinfo=UTC),
        source=EntrySource.MANUAL,
        created_at=NOW,
        updated_at=NOW,
        revision=revision,
        rounding_minutes=1,
    )


def legacy_rows(finish=8):
    payload = {
        "actual_started_at": "2026-10-02T07:00:00+00:00",
        "actual_ended_at": f"2026-10-02T{finish:02}:00:00+00:00",
        "testhuset_task_id": None,
        "dsb_allocation_id": None,
    }
    return [HEADER, ["work_session", "work", "1", NOW.isoformat(), canonical_json(payload)]]


class SharedSheet:
    def __init__(self, rows=None):
        self.legacy = rows if rows is not None else legacy_rows()
        self.events = []
        self.changes = []
        self.user_tab = [["=SUM(A1:A2)", "untouched"]]
        self.target = None
        self.fail = None

    def read_legacy_rows(self):
        return [row[:] for row in self.legacy]

    def read_migration_events(self):
        return tuple(self.events)

    def initialize_migration(self, target, events):
        if self.events:
            raise ValueError("Already initialized")
        self.target = target
        self.events.extend(events)

    def append_migration_events(self, events):
        self.events.extend(events)
        if self.fail == "completion" and any(e["kind"] == "cutover" for e in events):
            raise TimeoutError("Accepted completion, lost response")

    def read_changes(self):
        return tuple(self.changes)

    def read_problems(self):
        return ()

    def append_changes(self, changes):
        self.changes.extend(changes)
        if self.fail == "seed":
            raise TimeoutError("Accepted seeds, lost response")


def machine(tmp_path, name, sheet, local=None):
    module = importlib.import_module("qi_flow.application.sync_migration")
    database = SQLiteDatabase(tmp_path / f"{name}.sqlite3")
    database.initialize()

    def factory():
        return SQLiteUnitOfWork(database)

    if local is not None:
        with factory() as uow:
            uow.sessions.add(local)
    settings = GoogleSyncSettings(factory, Clock())
    settings.save(CONFIG)
    copies = []

    class Safety:
        def create_snapshot(self):
            with factory() as uow:
                records = module.local_snapshot(uow)
            snapshot = module.LocalSnapshot(f"verified-{name}-{len(copies)}.sqlite3", records)
            copies.append(snapshot)
            return snapshot

    migration = module.SyncMigration(
        factory,
        sheet,
        Safety(),
        Clock(),
        UuidIdentifierGenerator(),
        generation=settings.generation(),
    )
    return factory, migration, copies, settings


def test_cutover_requires_paused_writers_and_every_declared_machine(tmp_path):
    sheet = SharedSheet()
    factory, migration, copies, settings = machine(tmp_path, "a", sheet, work())
    with pytest.raises(ValueError, match="paused"):
        migration.begin(("a", "b"), writers_paused=False)
    assert sheet.events == []
    plan = migration.begin(("a", "b"), writers_paused=True)
    migration.contribute("a")
    with pytest.raises(ValueError, match="participant"):
        migration.complete("a")
    assert copies
    assert settings.active_target() is None
    assert sheet.legacy == legacy_rows()
    assert sheet.user_tab == [["=SUM(A1:A2)", "untouched"]]
    assert plan.participants == ("a", "b")
    with factory() as uow:
        assert not uow.sync_for(plan.target).get_state("migration_complete")


def test_three_divergent_snapshots_survive_as_conflicts_with_local_values_intact(tmp_path):
    sheet = SharedSheet()
    fa, a, _, sa = machine(tmp_path, "a", sheet, work(9, 2))
    fb, b, _, sb = machine(tmp_path, "b", sheet, work(10, 99))
    plan = a.begin(("a", "b"), writers_paused=True)
    b.begin(("a", "b"), writers_paused=True)
    a.contribute("a")
    b.contribute("b")
    a.complete("a")
    b.complete("b")
    assert sa.active_target() == sb.active_target() == plan.target
    for factory, finish in ((fa, 9), (fb, 10)):
        with factory() as uow:
            repo = uow.sync_for(plan.target)
            assert uow.sessions.get(SessionId("work")).actual_ended_at.hour == finish
            assert len(repo.conflicts()) == 1
            assert len(repo.conflicts()[0].head_ids) == 3
            assert repo.pending() == ()
            assert len(repo.heads(("work_session", "work"))) == 1
    assert len(sheet.changes) == 3


def test_identical_values_deduplicate_even_if_revision_or_creation_metadata_differ(tmp_path):
    # Revision is legacy bookkeeping, not causal proof or a logical payload difference.
    sheet = SharedSheet()
    _, a, _, _ = machine(tmp_path, "a", sheet, work(revision=7))
    fb, b, _, _ = machine(tmp_path, "b", sheet, replace(work(), created_at=NOW.replace(hour=11)))
    a.begin(("a", "b"), writers_paused=True)
    b.begin(("a", "b"), writers_paused=True)
    a.contribute("a")
    b.contribute("b")
    plan = a.complete("a")
    b.complete("b")
    assert len(sheet.changes) == 1
    with fb() as uow:
        assert not uow.sync_for(plan.target).conflicts()


def test_empty_machine_and_workbook_can_join_without_invented_records(tmp_path):
    sheet = SharedSheet([])
    factory, migration, _, settings = machine(tmp_path, "empty", sheet)
    migration.begin(("empty",), writers_paused=True)
    migration.contribute("empty")
    plan = migration.complete("empty")
    assert settings.active_target() == plan.target
    assert sheet.changes == []
    with factory() as uow:
        assert uow.sessions.list_all() == []


def test_unknown_legacy_format_is_preserved_and_blocks_seeds(tmp_path):
    rows = legacy_rows()
    rows[1][4] = canonical_json(
        {"actual_started_at": "2026-10-02T07:00:00+00:00", "future_schema": True}
    )
    sheet = SharedSheet(rows)
    factory, migration, _, _ = machine(tmp_path, "a", sheet, work())
    plan = migration.begin(("a",), writers_paused=True)
    migration.contribute("a")
    with pytest.raises(ValueError, match="legacy"):
        migration.complete("a")
    assert sheet.changes == []
    assert list(sheet.events[1]["row"]) == rows[0]
    with factory() as uow:
        assert uow.sync_for(plan.target).problems()
        assert active_target(uow) is None


@pytest.mark.parametrize("fail", ["seed", "completion"])
def test_crash_retry_reuses_seeds_and_verifies_completion_before_activation(tmp_path, fail):
    sheet = SharedSheet()
    _factory, migration, _, settings = machine(tmp_path, "a", sheet, work())
    plan = migration.begin(("a",), writers_paused=True)
    migration.contribute("a")
    sheet.fail = fail
    with pytest.raises(TimeoutError):
        migration.complete("a")
    ids = {c.change_id for c in sheet.changes}
    assert settings.active_target() is None
    sheet.fail = None
    migration.complete("a")
    assert {c.change_id for c in sheet.changes} == ids
    assert len(sheet.changes) == len(ids)
    assert settings.active_target() == plan.target
    assert len([e for e in sheet.events if e["kind"] == "cutover"]) == 1


def test_deletion_and_live_variant_are_both_kept(tmp_path):
    sheet = SharedSheet()
    factory, migration, _, _ = machine(
        tmp_path, "a", sheet, replace(work(revision=99), deleted_at=NOW)
    )
    migration.begin(("a",), writers_paused=True)
    migration.contribute("a")
    plan = migration.complete("a")
    with factory() as uow:
        assert uow.sessions.get(SessionId("work")).deleted_at == NOW
        assert len(uow.sync_for(plan.target).conflicts()) == 1
    assert {c.operation for c in sheet.changes} == {"upsert", "delete"}


def test_changed_local_snapshot_does_not_activate_and_keeps_new_changes(tmp_path):
    sheet = SharedSheet()
    factory, migration, _, settings = machine(tmp_path, "a", sheet, work())
    migration.begin(("a",), writers_paused=True)
    migration.contribute("a")
    with factory() as uow:
        uow.sessions.save(work(9, 2))
    with pytest.raises(ValueError, match="snapshot changed"):
        migration.complete("a")
    assert settings.active_target() is None
    with factory() as uow:
        assert uow.sessions.get(SessionId("work")).actual_ended_at.hour == 9


def test_renewed_legacy_write_blocks_cutover_and_post_cutover_append(tmp_path):
    sheet = SharedSheet()
    factory, migration, _, _ = machine(tmp_path, "a", sheet, work())
    migration.begin(("a",), writers_paused=True)
    migration.contribute("a")
    plan = migration.complete("a")
    sheet.legacy = legacy_rows(9)
    with pytest.raises(ValueError, match="V1"):
        migration.verify_legacy_frozen(plan.target)
    with factory() as uow:
        assert uow.sync_for(plan.target).problems()[0].reason == "legacy_writes_resumed"


def test_settings_change_during_remote_work_keeps_obsolete_binding_disabled(tmp_path):
    sheet = SharedSheet()
    _, migration, _, settings = machine(tmp_path, "a", sheet, work())
    migration.begin(("a",), writers_paused=True)
    migration.contribute("a")
    settings.disable()
    with pytest.raises(ValueError, match="settings"):
        migration.complete("a")
    assert settings.active_target() is None


def test_unsupported_migration_event_is_quarantined_and_cannot_be_ignored(tmp_path):
    sheet = SharedSheet()
    factory, migration, _, settings = machine(tmp_path, "a", sheet, work())
    plan = migration.begin(("a",), writers_paused=True)
    migration.contribute("a")
    sheet.events.append({"kind": "future_protocol", "payload": "preserve me"})
    with pytest.raises(ValueError, match="Unsupported migration"):
        migration.complete("a")
    assert sheet.changes == []
    assert settings.active_target() is None
    with factory() as uow:
        problem = uow.sync_for(plan.target).problems()[0]
        assert problem.reason == "unsupported_migration_event"
        assert problem.raw["payload"] == "preserve me"


def test_local_backup_failure_never_acknowledges_a_participant(tmp_path):
    sheet = SharedSheet()
    _, migration, _, _ = machine(tmp_path, "a", sheet, work())
    migration.begin(("a",), writers_paused=True)

    class FailedSafety:
        def create_snapshot(self):
            raise OSError("Cannot write verified copy")

    migration._safety = FailedSafety()
    with pytest.raises(OSError):
        migration.contribute("a")
    assert not any(e["kind"] == "snapshot_ack" for e in sheet.events)


def test_active_parent_and_ended_child_are_preserved_locally_and_not_seeded(tmp_path):
    from qi_flow.domain.models import Deduction, DeductionId, DeductionKind

    sheet = SharedSheet([])
    factory, migration, _, _ = machine(tmp_path, "a", sheet, replace(work(), actual_ended_at=None))
    with factory() as uow:
        uow.deductions.add(
            Deduction(
                DeductionId("child"),
                SessionId("work"),
                DeductionKind.LUNCH,
                work().actual_started_at,
                work().actual_ended_at,
                created_at=NOW,
                updated_at=NOW,
            )
        )
    migration.begin(("a",), writers_paused=True)
    migration.contribute("a")
    migration.complete("a")
    assert sheet.changes == []
    with factory() as uow:
        assert uow.sessions.get_active() is not None
        assert uow.deductions.get(DeductionId("child")) is not None
