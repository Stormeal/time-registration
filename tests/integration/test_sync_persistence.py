"""Durable target-bound sync state shares local mutation transactions."""

import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from importlib.resources import files
from pathlib import Path

import pytest

from qi_flow.application.sync_models import (
    SyncChange,
    SyncConflict,
    SyncContentError,
    SyncProblem,
    SyncPublication,
    SyncReviewError,
    SyncTarget,
    canonical_json,
    finalize_group,
)
from qi_flow.domain.models import DayDetails, SessionId, WorkLocation, WorkSession
from qi_flow.infrastructure.sqlite.database import SQLiteDatabase
from qi_flow.infrastructure.sqlite.repositories import SQLiteUnitOfWork

NOW = datetime(2026, 10, 3, 8, tzinfo=UTC)
A = SyncTarget("sheet-a", "log-a")
B = SyncTarget("sheet-b", "log-a")
C = SyncTarget("sheet-a", "recreated-log")


def group(identifier: str = "c1", **overrides: object) -> tuple[SyncChange, ...]:
    values = dict(
        change_id=identifier,
        schema_version=2,
        entity_kind="work_session",
        entity_id="s1",
        parent_ids=(),
        group_id="g-" + identifier,
        group_members=(identifier,),
        group_digest="",
        aggregate_base_heads={},
        operation="upsert",
        payload={"value": identifier},
        created_at=NOW,
        device_id="opaque-device",
    )
    values.update(overrides)
    return finalize_group((SyncChange(**values),))  # type: ignore[arg-type]


@pytest.fixture
def database(tmp_path: Path) -> SQLiteDatabase:
    result = SQLiteDatabase(tmp_path / "test.sqlite3")
    result.initialize()
    return result


def test_pending_survives_restart_and_acknowledges_only_exact_ids(database: SQLiteDatabase) -> None:
    first, second = group(), group("c2", parent_ids=("c1",))
    with SQLiteUnitOfWork(database) as uow:
        repo = uow.sync_for(A)
        repo.enqueue(first)
        repo.enqueue(first)
        repo.enqueue(second)
    with SQLiteUnitOfWork(SQLiteDatabase(database.database_file)) as uow:
        repo = uow.sync_for(A)
        assert repo.pending() == first + second
        repo.acknowledge(("c1",))
        assert repo.pending() == second
        assert repo.observed() == first + second
        with pytest.raises(ValueError):
            repo.acknowledge(("c2", "absent"))
        assert repo.pending() == second
    with SQLiteUnitOfWork(database) as uow:
        assert uow.sync_for(A).pending() == second


def test_every_protocol_state_is_bound_to_sheet_and_log(database: SQLiteDatabase) -> None:
    with SQLiteUnitOfWork(database) as uow:
        for target in (A, B, C):
            repo = uow.sync_for(target)
            repo.enqueue(group(payload={"sheet": target.spreadsheet_id, "log": target.log_id}))
        repo = uow.sync_for(A)
        repo.set_heads(("work_session", "s1"), ("c1",))
        repo.set_state("migration", {"reviewed": ["baseline-a"]})
        repo.save_conflict(SyncConflict("conflict", (("work_session", "s1"),), group(), "fork"))
        repo.record_problem(SyncProblem("problem", "malformed", "{broken", "row:7"))
        repo.defer(("c1",), NOW + timedelta(seconds=30))
        repo.mark_attempted(("c1",))
        for target in (B, C):
            other = uow.sync_for(target)
            assert other.heads(("work_session", "s1")) == ()
            assert other.get_state("migration") is None
            assert other.conflicts() == ()
            assert other.problems() == ()
            assert other.publication("c1") == SyncPublication()
        uow.sync_for(B).acknowledge(("c1",))
    with SQLiteUnitOfWork(database) as uow:
        assert len(uow.sync_for(A).pending()) == len(uow.sync_for(C).pending()) == 1
        assert uow.sync_for(B).pending() == ()


def test_observe_incomplete_group_stages_without_local_outbox_echo(
    database: SQLiteDatabase,
) -> None:
    complete = finalize_group((group()[0], replace(group("c2")[0], group_id="g-c1")))
    with SQLiteUnitOfWork(database) as uow:
        repo = uow.sync_for(A)
        repo.observe(complete[:1])
        repo.observe(complete[:1])
    with SQLiteUnitOfWork(database) as uow:
        repo = uow.sync_for(A)
        assert repo.observed() == complete[:1]
        assert repo.pending() == ()
        with pytest.raises(ValueError):
            repo.acknowledge(("c1",))
        repo.observe(complete[1:])
        assert repo.observed() == complete
        assert repo.pending() == ()


def test_local_corruption_refuses_entire_batch_without_overwriting(
    database: SQLiteDatabase,
) -> None:
    with SQLiteUnitOfWork(database) as uow:
        repo = uow.sync_for(A)
        repo.enqueue(group())
        with pytest.raises(SyncContentError):
            repo.enqueue(group("c2") + group(payload={"changed": True}))
        assert repo.pending() == group()


def test_remote_same_id_corruption_is_durable_quarantine_and_blocks_ack(
    database: SQLiteDatabase,
) -> None:
    differing = group(payload={"changed": True})
    with SQLiteUnitOfWork(database) as uow:
        repo = uow.sync_for(A)
        repo.enqueue(group())
        repo.observe(differing)
        repo.observe(differing)
    with SQLiteUnitOfWork(database) as uow:
        repo = uow.sync_for(A)
        assert repo.observed() == group()
        assert len(repo.problems()) == 1
        assert canonical_json(repo.problems()[0].raw) == differing[0].canonical_json()
        with pytest.raises(SyncContentError):
            repo.acknowledge(("c1",))
        assert repo.pending() == group()


def test_grace_and_attempted_publication_survive_restart_and_idempotent_enqueue(
    database: SQLiteDatabase,
) -> None:
    deadline = NOW + timedelta(seconds=30)
    with SQLiteUnitOfWork(database) as uow:
        repo = uow.sync_for(A)
        repo.enqueue(group())
        repo.defer(("c1",), deadline)
        repo.mark_attempted(("c1",))
    with SQLiteUnitOfWork(database) as uow:
        repo = uow.sync_for(A)
        repo.enqueue(group())
        assert repo.publication("c1") == SyncPublication(deadline, True)
        assert repo.pending() == group()
        with pytest.raises(ValueError):
            repo.defer(("c1",), NOW)


def test_conflict_closure_requires_all_current_reviewed_heads(database: SQLiteDatabase) -> None:
    conflict = SyncConflict("fork", (("work_session", "s1"),), group() + group("c2"), "fork")
    with SQLiteUnitOfWork(database) as uow:
        repo = uow.sync_for(A)
        repo.save_conflict(conflict)
    with SQLiteUnitOfWork(database) as uow:
        repo = uow.sync_for(A)
        with pytest.raises(SyncReviewError):
            repo.close_conflict("fork", frozenset({"c1"}))
        assert repo.conflicts() == (conflict,)
        repo.save_conflict(replace(conflict, changes=conflict.changes + group("c3")))
        with pytest.raises(SyncReviewError):
            repo.close_conflict("fork", frozenset({"c1", "c2"}))
        repo.close_conflict("fork", frozenset({"c1", "c2", "c3"}))
        assert repo.conflicts() == ()


def test_groups_cannot_be_enqueued_or_acknowledged_in_parts(database: SQLiteDatabase) -> None:
    complete = finalize_group((group()[0], replace(group("c2")[0], group_id="g-c1")))
    with SQLiteUnitOfWork(database) as uow:
        repo = uow.sync_for(A)
        with pytest.raises(ValueError, match="complete"):
            repo.enqueue(complete[:1])
        assert repo.observed() == ()
        repo.enqueue(complete)
        with pytest.raises(ValueError, match="complete"):
            repo.acknowledge(("c1",))
        assert repo.pending() == complete
        repo.acknowledge(("c1", "c2"))
        assert repo.pending() == ()


def test_reusing_group_id_cannot_publish_a_different_commit_envelope(
    database: SQLiteDatabase,
) -> None:
    with SQLiteUnitOfWork(database) as uow:
        repo = uow.sync_for(A)
        repo.enqueue(group())
        reused = group("c2", group_id="g-c1")
        with pytest.raises(SyncContentError):
            repo.enqueue(reused)
        assert repo.pending() == group()


def test_observed_group_identity_collision_prevents_ack_without_losing_either_variant(
    database: SQLiteDatabase,
) -> None:
    with SQLiteUnitOfWork(database) as uow:
        repo = uow.sync_for(A)
        repo.enqueue(group())
        reused = group("c2", group_id="g-c1")
        repo.observe(reused)
        assert repo.observed() == group() + reused
        with pytest.raises(ValueError):
            repo.acknowledge(("c1",))
        assert repo.pending() == group()


@pytest.mark.parametrize("failing_table", ["sync_outbox", "day_details"])
def test_local_mutation_and_outbox_roll_back_together(
    database: SQLiteDatabase,
    failing_table: str,
) -> None:
    with database.transaction() as connection:
        connection.execute(
            f"CREATE TRIGGER fail_insert BEFORE INSERT ON {failing_table} "
            "BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
        )
    with (
        pytest.raises(sqlite3.IntegrityError, match="injected failure"),
        SQLiteUnitOfWork(database) as uow,
    ):
        if failing_table == "sync_outbox":
            uow.days.save(DayDetails(date(2026, 10, 3), WorkLocation.OFFICE, "local"))
            uow.sync_for(A).enqueue(group())
        else:
            uow.sync_for(A).enqueue(group())
            uow.days.save(DayDetails(date(2026, 10, 3), WorkLocation.OFFICE, "local"))
    with SQLiteUnitOfWork(database) as uow:
        assert uow.days.list_all() == []
        assert uow.sync_for(A).pending() == ()
        assert uow.sync_for(A).observed() == ()


def test_tombstones_heads_protocol_state_and_raw_issues_survive_restart(
    database: SQLiteDatabase,
) -> None:
    with SQLiteUnitOfWork(database) as uow:
        repo = uow.sync_for(A)
        repo.observe(group(operation="delete", payload=None))
        repo.set_heads(("work_session", "s1"), ("c1",))
        repo.set_state("migration", {"baseline": ["legacy-one"]})
        repo.record_problem(SyncProblem("issue", "unsupported_schema", {"schema": 99}, "row:8"))
    with SQLiteUnitOfWork(database) as uow:
        repo = uow.sync_for(A)
        assert repo.observed()[0].operation == "delete"
        assert repo.heads(("work_session", "s1")) == ("c1",)
        assert repo.get_state("migration") == {"baseline": ("legacy-one",)}
        assert repo.problems() == (
            SyncProblem("issue", "unsupported_schema", {"schema": 99}, "row:8"),
        )
        with pytest.raises(ValueError):
            repo.set_heads(("work_session", "s1"), ("absent",))


def test_schema_six_copy_migrates_without_changing_existing_rows(tmp_path: Path) -> None:
    old = SQLiteDatabase(tmp_path / "original.sqlite3")
    with closing(old.connect()) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, name TEXT NOT NULL, "
            "applied_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        root = files("qi_flow.infrastructure.sqlite.migrations")
        for migration in sorted(root.iterdir(), key=lambda item: item.name):
            if migration.name.endswith(".sql") and int(migration.name[:4]) <= 6:
                connection.executescript(migration.read_text(encoding="utf-8"))
                connection.execute(
                    "INSERT INTO schema_migrations(version,name) VALUES (?,?)",
                    (int(migration.name[:4]), migration.name),
                )
        connection.commit()
    with SQLiteUnitOfWork(old) as uow:
        uow.sessions.add(WorkSession(SessionId("preserved"), NOW, created_at=NOW, updated_at=NOW))
        uow.days.save(DayDetails(date(2026, 10, 3), WorkLocation.OFFICE, "preserved"))
        uow.settings.save("preference", {"value": 15}, NOW)
        uow.audit.record("audit", "work_session", "preserved", "update", {"value": 1}, NOW)
    tables = (
        "work_sessions",
        "deductions",
        "day_details",
        "settings",
        "audit_entries",
        "weekly_targets",
    )
    with closing(old.connect()) as connection:
        before = {
            table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table}")]
            for table in tables
        }
        copied = SQLiteDatabase(tmp_path / "copied.sqlite3")
        with closing(copied.connect()) as destination:
            connection.backup(destination)
    copied.initialize()
    copied.initialize()
    with closing(copied.connect()) as connection:
        after = {
            table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table}")]
            for table in tables
        }
        assert before == after
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 7
    with SQLiteUnitOfWork(copied) as uow:
        assert uow.sync_for(A).pending() == ()
        assert uow.sync_for(A).get_state("migration") is None


def test_commit_failure_closes_transaction_and_rolls_back_local_and_outbox(
    database: SQLiteDatabase,
) -> None:
    with database.transaction() as connection:
        connection.execute("CREATE TABLE required_parent(id TEXT PRIMARY KEY)")
        connection.execute(
            "CREATE TABLE deferred_child(id TEXT REFERENCES required_parent(id) "
            "DEFERRABLE INITIALLY DEFERRED)"
        )
    uow = SQLiteUnitOfWork(database)
    with pytest.raises(sqlite3.IntegrityError), uow:
        connection = uow._connection
        assert connection is not None
        uow.days.save(DayDetails(date(2026, 10, 3), WorkLocation.OFFICE, "local"))
        uow.sync_for(A).enqueue(group())
        connection.execute("INSERT INTO deferred_child VALUES ('missing')")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")
    with SQLiteUnitOfWork(database) as reopened:
        assert reopened.days.list_all() == []
        assert reopened.sync_for(A).pending() == ()
