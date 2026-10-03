"""SQLite implementations of application persistence ports."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from types import TracebackType
from typing import Self

from qi_flow.application.ports import (
    AuditRepository,
    DayDetailsRepository,
    DeductionRepository,
    SettingsRepository,
    SyncRepository,
    WeeklyTargetRepository,
    WorkSessionRepository,
)
from qi_flow.application.sync_models import (
    EntityKey,
    SyncChange,
    SyncConflict,
    SyncContentError,
    SyncProblem,
    SyncPublication,
    SyncReviewError,
    SyncTarget,
    canonical_json,
    freeze_json,
    utc_instant,
    validate_group,
)
from qi_flow.domain.models import (
    DayDetails,
    Deduction,
    DeductionId,
    DeductionKind,
    EntrySource,
    IsoWeek,
    SessionId,
    WeeklyTarget,
    WorkLocation,
    WorkSession,
)
from qi_flow.infrastructure.sqlite.database import SQLiteDatabase


def _stamp(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None


def _read_stamp(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value).astimezone(UTC) if value is not None else None


def _session_from_row(row: sqlite3.Row) -> WorkSession:
    return WorkSession(
        id=SessionId(row["id"]),
        actual_started_at=_read_stamp(row["actual_started_at_utc"]),  # type: ignore[arg-type]
        actual_ended_at=_read_stamp(row["actual_ended_at_utc"]),
        effective_started_at=_read_stamp(row["effective_started_at_utc"]),
        effective_ended_at=_read_stamp(row["effective_ended_at_utc"]),
        source=EntrySource(row["source"]),
        revision=row["revision"],
        created_at=_read_stamp(row["created_at_utc"]),
        updated_at=_read_stamp(row["updated_at_utc"]),
        deleted_at=_read_stamp(row["deleted_at_utc"]),
        recovery_acknowledged_at=_read_stamp(row["recovery_acknowledged_at_utc"]),
        rounding_minutes=row["rounding_minutes"],
        testhuset_task_id=row["testhuset_task_id"],
        dsb_allocation_id=row["dsb_allocation_id"],
    )


def _deduction_from_row(row: sqlite3.Row) -> Deduction:
    return Deduction(
        id=DeductionId(row["id"]),
        session_id=SessionId(row["session_id"]),
        kind=DeductionKind(row["kind"]),
        actual_started_at=_read_stamp(row["actual_started_at_utc"]),  # type: ignore[arg-type]
        actual_ended_at=_read_stamp(row["actual_ended_at_utc"]),
        effective_started_at=_read_stamp(row["effective_started_at_utc"]),
        effective_ended_at=_read_stamp(row["effective_ended_at_utc"]),
        source=EntrySource(row["source"]),
        revision=row["revision"],
        created_at=_read_stamp(row["created_at_utc"]),
        updated_at=_read_stamp(row["updated_at_utc"]),
        deleted_at=_read_stamp(row["deleted_at_utc"]),
        rounding_minutes=row["rounding_minutes"],
    )


class SQLiteWorkSessionRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def add(self, session: WorkSession) -> None:
        self._connection.execute(
            """INSERT INTO work_sessions (
                id, actual_started_at_utc, actual_ended_at_utc, effective_started_at_utc,
                effective_ended_at_utc, source, revision, created_at_utc, updated_at_utc,
                deleted_at_utc, recovery_acknowledged_at_utc, rounding_minutes, testhuset_task_id,
                dsb_allocation_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            self._values(session),
        )

    def get(self, session_id: SessionId) -> WorkSession | None:
        row = self._connection.execute(
            "SELECT * FROM work_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return _session_from_row(row) if row is not None else None

    def get_active(self) -> WorkSession | None:
        row = self._connection.execute(
            "SELECT * FROM work_sessions "
            "WHERE actual_ended_at_utc IS NULL AND deleted_at_utc IS NULL"
        ).fetchone()
        return _session_from_row(row) if row is not None else None

    def save(self, session: WorkSession) -> None:
        self._connection.execute(
            """UPDATE work_sessions SET actual_started_at_utc=?, actual_ended_at_utc=?,
            effective_started_at_utc=?, effective_ended_at_utc=?, source=?, revision=?,
            created_at_utc=?, updated_at_utc=?, deleted_at_utc=?, recovery_acknowledged_at_utc=?,
            rounding_minutes=?, testhuset_task_id=?, dsb_allocation_id=?
            WHERE id=?""",
            (*self._values(session)[1:], session.id),
        )

    def list_intersecting(self, start: datetime, end: datetime) -> list[WorkSession]:
        rows = self._connection.execute(
            """SELECT * FROM work_sessions WHERE deleted_at_utc IS NULL
            AND actual_started_at_utc < ?
            AND (actual_ended_at_utc IS NULL OR actual_ended_at_utc > ?)""",
            (_stamp(end), _stamp(start)),
        ).fetchall()
        return [_session_from_row(row) for row in rows]

    def list_all(self) -> list[WorkSession]:
        rows = self._connection.execute("SELECT * FROM work_sessions").fetchall()
        return [_session_from_row(row) for row in rows]

    @staticmethod
    def _values(session: WorkSession) -> tuple[object, ...]:
        return (
            session.id,
            _stamp(session.actual_started_at),
            _stamp(session.actual_ended_at),
            _stamp(session.effective_started_at),
            _stamp(session.effective_ended_at),
            session.source,
            session.revision,
            _stamp(session.created_at),
            _stamp(session.updated_at),
            _stamp(session.deleted_at),
            _stamp(session.recovery_acknowledged_at),
            session.rounding_minutes,
            session.testhuset_task_id,
            session.dsb_allocation_id,
        )


class SQLiteDeductionRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def add(self, deduction: Deduction) -> None:
        self._connection.execute(
            """INSERT INTO deductions (id, session_id, kind, actual_started_at_utc,
            actual_ended_at_utc, effective_started_at_utc, effective_ended_at_utc, source,
            revision, created_at_utc, updated_at_utc, deleted_at_utc, rounding_minutes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            self._values(deduction),
        )

    def get_active(self, session_id: SessionId) -> Deduction | None:
        row = self._connection.execute(
            """SELECT * FROM deductions WHERE session_id=? AND actual_ended_at_utc IS NULL
            AND deleted_at_utc IS NULL""",
            (session_id,),
        ).fetchone()
        return _deduction_from_row(row) if row is not None else None

    def get(self, deduction_id: DeductionId) -> Deduction | None:
        row = self._connection.execute(
            "SELECT * FROM deductions WHERE id=?", (deduction_id,)
        ).fetchone()
        return _deduction_from_row(row) if row is not None else None

    def save(self, deduction: Deduction) -> None:
        self._connection.execute(
            """UPDATE deductions SET session_id=?, kind=?, actual_started_at_utc=?,
            actual_ended_at_utc=?, effective_started_at_utc=?, effective_ended_at_utc=?,
            source=?, revision=?, created_at_utc=?, updated_at_utc=?, deleted_at_utc=?,
            rounding_minutes=?
            WHERE id=?""",
            (*self._values(deduction)[1:], deduction.id),
        )

    def list_for_session(self, session_id: SessionId) -> list[Deduction]:
        rows = self._connection.execute(
            "SELECT * FROM deductions WHERE session_id=? ORDER BY actual_started_at_utc",
            (session_id,),
        ).fetchall()
        return [_deduction_from_row(row) for row in rows]

    def list_all(self) -> list[Deduction]:
        rows = self._connection.execute("SELECT * FROM deductions").fetchall()
        return [_deduction_from_row(row) for row in rows]

    @staticmethod
    def _values(deduction: Deduction) -> tuple[object, ...]:
        return (
            deduction.id,
            deduction.session_id,
            deduction.kind,
            _stamp(deduction.actual_started_at),
            _stamp(deduction.actual_ended_at),
            _stamp(deduction.effective_started_at),
            _stamp(deduction.effective_ended_at),
            deduction.source,
            deduction.revision,
            _stamp(deduction.created_at),
            _stamp(deduction.updated_at),
            _stamp(deduction.deleted_at),
            deduction.rounding_minutes,
        )


class SQLiteDayDetailsRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get(self, work_date: date) -> DayDetails | None:
        row = self._connection.execute(
            "SELECT * FROM day_details WHERE work_date=?", (work_date.isoformat(),)
        ).fetchone()
        return (
            DayDetails(
                date.fromisoformat(row["work_date"]),
                WorkLocation(row["location"]),
                row["note"],
                row["revision"],
            )
            if row
            else None
        )

    def save(self, details: DayDetails) -> None:
        self._connection.execute(
            """INSERT INTO day_details(work_date, location, note, revision, updated_at_utc)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(work_date) DO UPDATE SET location=excluded.location, note=excluded.note,
            revision=excluded.revision, updated_at_utc=CURRENT_TIMESTAMP""",
            (details.work_date.isoformat(), details.location, details.note, details.revision),
        )

    def list_all(self) -> list[DayDetails]:
        rows = self._connection.execute("SELECT * FROM day_details").fetchall()
        return [
            DayDetails(
                date.fromisoformat(row["work_date"]),
                WorkLocation(row["location"]),
                row["note"],
                row["revision"],
            )
            for row in rows
        ]


class SQLiteSettingsRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get(self, key: str) -> object | None:
        row = self._connection.execute(
            "SELECT value_json FROM settings WHERE key=?", (key,)
        ).fetchone()
        return json.loads(row["value_json"]) if row is not None else None

    def save(self, key: str, value: object, updated_at: datetime) -> None:
        self._connection.execute(
            """INSERT INTO settings(key, value_json, updated_at_utc) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,
            updated_at_utc=excluded.updated_at_utc""",
            (key, json.dumps(value), _stamp(updated_at)),
        )


class SQLiteAuditRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def record(
        self,
        audit_id: str,
        entity_type: str,
        entity_id: str,
        action: str,
        before_state: dict[str, object],
        created_at: datetime,
    ) -> None:
        self._connection.execute(
            """INSERT INTO audit_entries(
            id, entity_type, entity_id, action, before_state_json, created_at_utc, expires_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                audit_id,
                entity_type,
                entity_id,
                action,
                json.dumps(before_state),
                _stamp(created_at),
                _stamp(created_at + timedelta(days=30)),
            ),
        )

    def latest(self, entity_type: str, entity_id: str) -> dict[str, object] | None:
        row = self._connection.execute(
            """SELECT before_state_json FROM audit_entries
            WHERE entity_type=? AND entity_id=? AND expires_at_utc > CURRENT_TIMESTAMP
            ORDER BY created_at_utc DESC LIMIT 1""",
            (entity_type, entity_id),
        ).fetchone()
        return json.loads(row["before_state_json"]) if row is not None else None

    def list_active(self, as_of: datetime) -> list[dict[str, object]]:
        rows = self._connection.execute(
            """SELECT id, entity_type, entity_id, action, before_state_json, created_at_utc
            FROM audit_entries WHERE expires_at_utc > ? ORDER BY created_at_utc DESC""",
            (_stamp(as_of),),
        ).fetchall()
        return [self._history_row(row) for row in rows]

    def get_active(self, audit_id: str, as_of: datetime) -> dict[str, object] | None:
        row = self._connection.execute(
            """SELECT id, entity_type, entity_id, action, before_state_json, created_at_utc
            FROM audit_entries WHERE id=? AND expires_at_utc > ?""",
            (audit_id, _stamp(as_of)),
        ).fetchone()
        return self._history_row(row) if row is not None else None

    @staticmethod
    def _history_row(row: sqlite3.Row) -> dict[str, object]:
        return {
            "audit_id": str(row["id"]),
            "entity_type": str(row["entity_type"]),
            "entity_id": str(row["entity_id"]),
            "action": str(row["action"]),
            "changed_at": datetime.fromisoformat(str(row["created_at_utc"])),
            "before_state": json.loads(row["before_state_json"]),
        }


class SQLiteWeeklyTargetRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get(self, iso_week: IsoWeek) -> WeeklyTarget | None:
        row = self._connection.execute(
            "SELECT target_minutes FROM weekly_targets WHERE iso_year=? AND iso_week=?",
            (iso_week.year, iso_week.week),
        ).fetchone()
        return WeeklyTarget(iso_week, row["target_minutes"]) if row is not None else None

    def save(self, target: WeeklyTarget, updated_at: datetime) -> None:
        self._connection.execute(
            """INSERT INTO weekly_targets(iso_year, iso_week, target_minutes, updated_at_utc)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(iso_year, iso_week) DO UPDATE SET target_minutes=excluded.target_minutes,
            updated_at_utc=excluded.updated_at_utc""",
            (target.iso_week.year, target.iso_week.week, target.target_minutes, _stamp(updated_at)),
        )


class SQLiteSyncRepository:
    """An immutable target namespace on the caller's existing SQLite transaction.

    Observations include local changes; only explicit enqueue authors an outbox row.
    Corrupt observations retain both the first content and a quarantined incoming variant.
    The caller must commit that evidence, then report the problem outside the transaction.
    """

    def __init__(self, connection: sqlite3.Connection, target: SyncTarget) -> None:
        self._connection = connection
        self._namespace = (target.spreadsheet_id, target.log_id)

    def pending(self) -> tuple[SyncChange, ...]:
        rows = self._connection.execute(
            """SELECT c.canonical_json FROM sync_changes c
            JOIN sync_outbox o USING (spreadsheet_id, log_id, change_id)
            LEFT JOIN sync_acknowledgements a USING (spreadsheet_id, log_id, change_id)
            WHERE c.spreadsheet_id=? AND c.log_id=? AND a.change_id IS NULL
            ORDER BY o.rowid""",
            self._namespace,
        )
        return tuple(SyncChange.from_json(row[0]) for row in rows)

    def observed(self) -> tuple[SyncChange, ...]:
        rows = self._connection.execute(
            """SELECT canonical_json FROM sync_changes
            WHERE spreadsheet_id=? AND log_id=? ORDER BY rowid""",
            self._namespace,
        )
        return tuple(SyncChange.from_json(row[0]) for row in rows)

    def _stored(self, change_id: str) -> str | None:
        row = self._connection.execute(
            """SELECT canonical_json FROM sync_changes
            WHERE spreadsheet_id=? AND log_id=? AND change_id=?""",
            (*self._namespace, change_id),
        ).fetchone()
        return str(row[0]) if row is not None else None

    @staticmethod
    def _complete_groups(changes: Sequence[SyncChange]) -> None:
        groups: dict[str, list[SyncChange]] = {}
        for change in changes:
            groups.setdefault(change.group_id, []).append(change)
        for group in groups.values():
            validate_group(group)

    def enqueue(self, changes: Sequence[SyncChange]) -> None:
        # Validate the entire batch before inserting any row, including when the caller
        # catches a domain refusal and continues using the surrounding transaction.
        unique: dict[str, SyncChange] = {}
        for change in changes:
            previous = unique.get(change.change_id)
            content = change.canonical_json()
            stored = self._stored(change.change_id)
            if (previous is not None and previous.canonical_json() != content) or (
                stored is not None and stored != content
            ):
                raise SyncContentError("A sync change ID already has different content.")
            unique[change.change_id] = change
        self._complete_groups(tuple(unique.values()))
        envelopes = {change.group_id: change for change in unique.values()}
        for stored_change in self.observed():
            envelope = envelopes.get(stored_change.group_id)
            if envelope is not None and (
                stored_change.group_members != envelope.group_members
                or stored_change.group_digest != envelope.group_digest
                or stored_change.aggregate_base_heads != envelope.aggregate_base_heads
            ):
                raise SyncContentError("A sync group ID already has a different commit envelope.")
        for change in unique.values():
            self._insert_change(change)
            self._connection.execute(
                """INSERT OR IGNORE INTO sync_outbox(spreadsheet_id, log_id, change_id)
                VALUES (?, ?, ?)""",
                (*self._namespace, change.change_id),
            )

    def _insert_change(self, change: SyncChange) -> None:
        self._connection.execute(
            """INSERT OR IGNORE INTO sync_changes
            (spreadsheet_id, log_id, change_id, canonical_json) VALUES (?, ?, ?, ?)""",
            (*self._namespace, change.change_id, change.canonical_json()),
        )

    def observe(self, changes: Sequence[SyncChange]) -> None:
        for change in changes:
            stored = self._stored(change.change_id)
            if stored is not None and stored != change.canonical_json():
                self.record_problem(
                    SyncProblem(
                        "duplicate-content:" + change.content_digest(),
                        "duplicate_change_id",
                        change.to_record(),
                        "change:" + change.change_id,
                    )
                )
            else:
                self._insert_change(change)

    def acknowledge(self, change_ids: Sequence[str]) -> None:
        if self.problems():
            raise SyncContentError("Resolve quarantined sync data before acknowledging changes.")
        changes: list[SyncChange] = []
        for change_id in dict.fromkeys(change_ids):
            # A remote observation cannot become a publication acknowledgement.
            self.publication(change_id)
            stored = self._stored(change_id)
            assert stored is not None  # outbox foreign key guarantees this
            changes.append(SyncChange.from_json(stored))
        self._complete_groups(changes)
        selected_groups = {change.group_id for change in changes}
        self._complete_groups(
            tuple(change for change in self.observed() if change.group_id in selected_groups)
        )
        self._connection.executemany(
            """INSERT OR IGNORE INTO sync_acknowledgements(spreadsheet_id, log_id, change_id)
            VALUES (?, ?, ?)""",
            [(*self._namespace, change.change_id) for change in changes],
        )

    def heads(self, entity_key: EntityKey) -> tuple[str, ...]:
        row = self._connection.execute(
            """SELECT head_ids_json FROM sync_heads
            WHERE spreadsheet_id=? AND log_id=? AND entity_kind=? AND entity_id=?""",
            (*self._namespace, *entity_key),
        ).fetchone()
        return tuple(json.loads(row[0])) if row is not None else ()

    def set_heads(self, entity_key: EntityKey, head_ids: Sequence[str]) -> None:
        heads = tuple(sorted(set(head_ids)))
        for head_id in heads:
            content = self._stored(head_id)
            if content is None or SyncChange.from_json(content).entity_key != entity_key:
                raise ValueError("Materialized heads must reference known changes of this entity.")
        self._connection.execute(
            """INSERT INTO sync_heads
            (spreadsheet_id, log_id, entity_kind, entity_id, head_ids_json) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(spreadsheet_id, log_id, entity_kind, entity_id)
            DO UPDATE SET head_ids_json=excluded.head_ids_json""",
            (*self._namespace, *entity_key, canonical_json(heads)),
        )

    def publication(self, change_id: str) -> SyncPublication:
        row = self._connection.execute(
            """SELECT not_before_utc, attempted FROM sync_outbox
            WHERE spreadsheet_id=? AND log_id=? AND change_id=?""",
            (*self._namespace, change_id),
        ).fetchone()
        if row is None:
            raise ValueError("Publication metadata requires a locally enqueued change.")
        return SyncPublication(_read_stamp(row[0]), bool(row[1]))

    def _pending_ids(self, change_ids: Sequence[str]) -> tuple[str, ...]:
        identifiers = tuple(dict.fromkeys(change_ids))
        pending = {change.change_id for change in self.pending()}
        if not set(identifiers) <= pending:
            raise ValueError("Publication state requires pending local change IDs.")
        return identifiers

    def defer(self, change_ids: Sequence[str], not_before: datetime) -> None:
        stamp = utc_instant(not_before).isoformat()
        identifiers = self._pending_ids(change_ids)
        if any(self.publication(change_id).attempted for change_id in identifiers):
            raise ValueError("An attempted publication cannot be treated as unpublished.")
        self._connection.executemany(
            """UPDATE sync_outbox SET not_before_utc=?
            WHERE spreadsheet_id=? AND log_id=? AND change_id=?""",
            [(stamp, *self._namespace, change_id) for change_id in identifiers],
        )

    def mark_attempted(self, change_ids: Sequence[str]) -> None:
        identifiers = self._pending_ids(change_ids)
        self._connection.executemany(
            """UPDATE sync_outbox SET attempted=1
            WHERE spreadsheet_id=? AND log_id=? AND change_id=?""",
            [(*self._namespace, change_id) for change_id in identifiers],
        )

    @staticmethod
    def _conflict_json(conflict: SyncConflict) -> str:
        return canonical_json(
            {
                "conflict_id": conflict.conflict_id,
                "entity_keys": conflict.entity_keys,
                "changes": [change.to_record() for change in conflict.changes],
                "reason": conflict.reason,
            }
        )

    @staticmethod
    def _read_conflict(content: str) -> SyncConflict:
        record = json.loads(content)
        return SyncConflict(
            record["conflict_id"],
            tuple(tuple(key) for key in record["entity_keys"]),
            tuple(SyncChange.from_json(canonical_json(change)) for change in record["changes"]),
            record["reason"],
        )

    def save_conflict(self, conflict: SyncConflict) -> None:
        self._connection.execute(
            """INSERT INTO sync_conflicts(spreadsheet_id, log_id, conflict_id, content_json)
            VALUES (?, ?, ?, ?) ON CONFLICT(spreadsheet_id, log_id, conflict_id)
            DO UPDATE SET content_json=excluded.content_json, closed=0
            WHERE sync_conflicts.content_json != excluded.content_json""",
            (*self._namespace, conflict.conflict_id, self._conflict_json(conflict)),
        )

    def close_conflict(self, conflict_id: str, reviewed_head_ids: frozenset[str]) -> None:
        row = self._connection.execute(
            """SELECT content_json FROM sync_conflicts
            WHERE spreadsheet_id=? AND log_id=? AND conflict_id=? AND closed=0""",
            (*self._namespace, conflict_id),
        ).fetchone()
        if row is None or self._read_conflict(row[0]).head_ids != reviewed_head_ids:
            raise SyncReviewError("Conflict changed; review every current head before resolving.")
        self._connection.execute(
            """UPDATE sync_conflicts SET closed=1
            WHERE spreadsheet_id=? AND log_id=? AND conflict_id=?""",
            (*self._namespace, conflict_id),
        )

    def conflicts(self) -> tuple[SyncConflict, ...]:
        rows = self._connection.execute(
            """SELECT content_json FROM sync_conflicts
            WHERE spreadsheet_id=? AND log_id=? AND closed=0 ORDER BY rowid""",
            self._namespace,
        )
        return tuple(self._read_conflict(row[0]) for row in rows)

    def get_state(self, key: str) -> object:
        row = self._connection.execute(
            "SELECT value_json FROM sync_state WHERE spreadsheet_id=? AND log_id=? AND key=?",
            (*self._namespace, key),
        ).fetchone()
        return freeze_json(json.loads(row[0])) if row is not None else None

    def set_state(self, key: str, value: object) -> None:
        self._connection.execute(
            """INSERT INTO sync_state(spreadsheet_id, log_id, key, value_json) VALUES (?, ?, ?, ?)
            ON CONFLICT(spreadsheet_id, log_id, key)
            DO UPDATE SET value_json=excluded.value_json""",
            (*self._namespace, key, canonical_json(value)),
        )

    def record_problem(self, problem: SyncProblem) -> None:
        content = canonical_json(
            {
                "problem_id": problem.problem_id,
                "reason": problem.reason,
                "raw": problem.raw,
                "source": problem.source,
            }
        )
        existing = self._connection.execute(
            """SELECT content_json FROM sync_problems
            WHERE spreadsheet_id=? AND log_id=? AND problem_id=?""",
            (*self._namespace, problem.problem_id),
        ).fetchone()
        if existing is not None and existing[0] != content:
            raise SyncContentError("A sync problem ID already has different content.")
        self._connection.execute(
            """INSERT OR IGNORE INTO sync_problems
            (spreadsheet_id, log_id, problem_id, content_json) VALUES (?, ?, ?, ?)""",
            (*self._namespace, problem.problem_id, content),
        )

    def problems(self) -> tuple[SyncProblem, ...]:
        rows = self._connection.execute(
            """SELECT content_json FROM sync_problems
            WHERE spreadsheet_id=? AND log_id=? ORDER BY rowid""",
            self._namespace,
        )
        return tuple(SyncProblem(**json.loads(row[0])) for row in rows)


class SQLiteUnitOfWork:
    """A short-lived, explicit SQLite transaction exposing repository adapters."""

    sessions: WorkSessionRepository
    deductions: DeductionRepository
    days: DayDetailsRepository
    settings: SettingsRepository
    audit: AuditRepository
    weekly_targets: WeeklyTargetRepository

    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database
        self._connection: sqlite3.Connection | None = None

    def __enter__(self) -> Self:
        self._connection = self._database.connect()
        self._connection.execute("BEGIN IMMEDIATE")
        self.sessions = SQLiteWorkSessionRepository(self._connection)
        self.deductions = SQLiteDeductionRepository(self._connection)
        self.days = SQLiteDayDetailsRepository(self._connection)
        self.settings = SQLiteSettingsRepository(self._connection)
        self.audit = SQLiteAuditRepository(self._connection)
        self.weekly_targets = SQLiteWeeklyTargetRepository(self._connection)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._connection is not None:
            try:
                if exc_type is None:
                    self._connection.commit()
                else:
                    self._connection.rollback()
            finally:
                # Close also rolls back a rejected commit and releases its write lock.
                self._connection.close()

    def sync_for(self, target: SyncTarget) -> SyncRepository:
        if self._connection is None:
            raise RuntimeError("Sync repositories require an open unit of work.")
        return SQLiteSyncRepository(self._connection, target)

    def commit(self) -> None:
        # The context manager owns the final commit, keeping exception rollback reliable.
        return None

    def rollback(self) -> None:
        if self._connection is not None:
            self._connection.rollback()
