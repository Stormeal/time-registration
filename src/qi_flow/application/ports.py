"""Ports implemented by infrastructure and fakes."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from types import TracebackType
from typing import Any, Protocol, Self

from qi_flow.application.sync_models import (
    EntityKey,
    SyncChange,
    SyncConflict,
    SyncProblem,
    SyncPublication,
    SyncTarget,
)
from qi_flow.domain.models import (
    DayDetails,
    Deduction,
    DeductionId,
    IsoWeek,
    SessionId,
    WeeklyTarget,
    WorkSession,
)


class Clock(Protocol):
    """Source of timezone-aware UTC instants."""

    def now(self) -> datetime: ...


class IdentifierGenerator(Protocol):
    """Source of stable entity identifiers."""

    def session_id(self) -> SessionId: ...

    def deduction_id(self) -> DeductionId: ...

    def audit_id(self) -> str: ...

    def change_id(self) -> str: ...

    def group_id(self) -> str: ...

    def conflict_id(self) -> str: ...


class WorkSessionRepository(Protocol):
    def add(self, session: WorkSession) -> None: ...

    def get(self, session_id: SessionId) -> WorkSession | None: ...

    def get_active(self) -> WorkSession | None: ...

    def save(self, session: WorkSession) -> None: ...

    def list_intersecting(self, start: datetime, end: datetime) -> list[WorkSession]: ...

    def list_all(self) -> list[WorkSession]: ...


class DeductionRepository(Protocol):
    def add(self, deduction: Deduction) -> None: ...

    def get_active(self, session_id: SessionId) -> Deduction | None: ...

    def save(self, deduction: Deduction) -> None: ...

    def list_for_session(self, session_id: SessionId) -> list[Deduction]: ...

    def list_all(self) -> list[Deduction]: ...

    def get(self, deduction_id: DeductionId) -> Deduction | None: ...


class DayDetailsRepository(Protocol):
    def get(self, work_date: date) -> DayDetails | None: ...

    def save(self, details: DayDetails) -> None: ...

    def list_all(self) -> list[DayDetails]: ...


class SettingsRepository(Protocol):
    def get(self, key: str) -> Any | None: ...

    def save(self, key: str, value: Any, updated_at: datetime) -> None: ...


class AuditRepository(Protocol):
    def record(
        self,
        audit_id: str,
        entity_type: str,
        entity_id: str,
        action: str,
        before_state: dict[str, Any],
        created_at: datetime,
    ) -> None: ...

    def latest(self, entity_type: str, entity_id: str) -> dict[str, Any] | None: ...

    def list_active(self, as_of: datetime) -> list[dict[str, Any]]: ...

    def get_active(self, audit_id: str, as_of: datetime) -> dict[str, Any] | None: ...


class WeeklyTargetRepository(Protocol):
    def get(self, iso_week: IsoWeek) -> WeeklyTarget | None: ...

    def save(self, target: WeeklyTarget, updated_at: datetime) -> None: ...


class SyncGateway(Protocol):
    """One immutable destination; invalid rows accompany valid observations."""

    def read_changes(self) -> tuple[SyncChange, ...]: ...

    def read_problems(self) -> tuple[SyncProblem, ...]:
        """Return raw issues from the most recent read, without another network request."""
        ...

    def append_changes(self, changes: Sequence[SyncChange]) -> None: ...


class SyncRepository(Protocol):
    """Target-bound durable state; mutations share the enclosing local transaction."""

    def pending(self) -> tuple[SyncChange, ...]: ...

    def observed(self) -> tuple[SyncChange, ...]:
        """All known graph changes, including locally authored changes."""
        ...

    def enqueue(self, changes: Sequence[SyncChange]) -> None: ...

    def observe(self, changes: Sequence[SyncChange]) -> None:
        """Stage even incomplete groups; quarantine differing duplicate IDs without raising."""
        ...

    def acknowledge(self, change_ids: Sequence[str]) -> None:
        """Only after caller readback verification of exact pending complete groups."""
        ...

    def save_conflict(self, conflict: SyncConflict) -> None: ...

    def close_conflict(self, conflict_id: str, reviewed_head_ids: frozenset[str]) -> None: ...

    def conflicts(self) -> tuple[SyncConflict, ...]: ...

    def heads(self, entity_key: EntityKey) -> tuple[str, ...]:
        """Materialized local payload provenance, not every observed graph tip."""
        ...

    def set_heads(self, entity_key: EntityKey, head_ids: Sequence[str]) -> None: ...

    def publication(self, change_id: str) -> SyncPublication: ...

    def defer(self, change_ids: Sequence[str], not_before: datetime) -> None: ...

    def mark_attempted(self, change_ids: Sequence[str]) -> None:
        """Commit before network publication; never reset on timeout, retry, or restart."""
        ...

    def get_state(self, key: str) -> object: ...

    def set_state(self, key: str, value: object) -> None: ...

    def record_problem(self, problem: SyncProblem) -> None: ...

    def problems(self) -> tuple[SyncProblem, ...]: ...


class UnitOfWork(Protocol):
    """Atomic persistence boundary for one application operation."""

    sessions: WorkSessionRepository
    deductions: DeductionRepository
    days: DayDetailsRepository
    settings: SettingsRepository
    audit: AuditRepository
    weekly_targets: WeeklyTargetRepository

    def sync_for(self, target: SyncTarget) -> SyncRepository: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...
