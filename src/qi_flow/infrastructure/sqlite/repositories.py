"""SQLite implementations of application persistence ports."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime
from types import TracebackType
from typing import Self

from qi_flow.application.ports import (
    DayDetailsRepository,
    DeductionRepository,
    SettingsRepository,
    WorkSessionRepository,
)
from qi_flow.domain.models import (
    DayDetails,
    Deduction,
    DeductionId,
    DeductionKind,
    EntrySource,
    SessionId,
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
                deleted_at_utc, recovery_acknowledged_at_utc, rounding_minutes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
            rounding_minutes=?
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


class SQLiteUnitOfWork:
    """A short-lived, explicit SQLite transaction exposing repository adapters."""

    sessions: WorkSessionRepository
    deductions: DeductionRepository
    days: DayDetailsRepository
    settings: SettingsRepository

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
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._connection is not None:
            if exc_type is None:
                self._connection.commit()
            else:
                self._connection.rollback()
            self._connection.close()

    def commit(self) -> None:
        # The context manager owns the final commit, keeping exception rollback reliable.
        return None

    def rollback(self) -> None:
        if self._connection is not None:
            self._connection.rollback()
