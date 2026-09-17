"""Ports implemented by infrastructure and fakes."""

from __future__ import annotations

from datetime import date, datetime
from types import TracebackType
from typing import Any, Protocol, Self

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


class WorkSessionRepository(Protocol):
    def add(self, session: WorkSession) -> None: ...

    def get(self, session_id: SessionId) -> WorkSession | None: ...

    def get_active(self) -> WorkSession | None: ...

    def save(self, session: WorkSession) -> None: ...

    def list_intersecting(self, start: datetime, end: datetime) -> list[WorkSession]: ...


class DeductionRepository(Protocol):
    def add(self, deduction: Deduction) -> None: ...

    def get_active(self, session_id: SessionId) -> Deduction | None: ...

    def save(self, deduction: Deduction) -> None: ...

    def list_for_session(self, session_id: SessionId) -> list[Deduction]: ...

    def get(self, deduction_id: DeductionId) -> Deduction | None: ...


class DayDetailsRepository(Protocol):
    def get(self, work_date: date) -> DayDetails | None: ...

    def save(self, details: DayDetails) -> None: ...


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


class WeeklyTargetRepository(Protocol):
    def get(self, iso_week: IsoWeek) -> WeeklyTarget | None: ...

    def save(self, target: WeeklyTarget, updated_at: datetime) -> None: ...


class UnitOfWork(Protocol):
    """Atomic persistence boundary for one application operation."""

    sessions: WorkSessionRepository
    deductions: DeductionRepository
    days: DayDetailsRepository
    settings: SettingsRepository
    audit: AuditRepository
    weekly_targets: WeeklyTargetRepository

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...
