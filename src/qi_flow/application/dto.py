"""Immutable command and query records crossing the application boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from qi_flow.domain.models import DeductionId, DeductionKind, SessionId, WorkLocation


@dataclass(frozen=True, slots=True)
class StartWorkCommand:
    occurred_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class FinishWorkCommand:
    occurred_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class StartDeductionCommand:
    kind: DeductionKind
    occurred_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class FinishDeductionCommand:
    occurred_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class UpdateDayDetailsCommand:
    work_date: date
    location: WorkLocation
    note: str


@dataclass(frozen=True, slots=True)
class ManualWorkSessionCommand:
    started_at: datetime
    ended_at: datetime


@dataclass(frozen=True, slots=True)
class ManualDeductionCommand:
    session_id: SessionId
    kind: DeductionKind
    started_at: datetime
    ended_at: datetime


@dataclass(frozen=True, slots=True)
class UpdateWorkSessionCommand:
    session_id: SessionId
    started_at: datetime
    ended_at: datetime


@dataclass(frozen=True, slots=True)
class UpdateDeductionCommand:
    deduction_id: DeductionId
    started_at: datetime
    ended_at: datetime


@dataclass(frozen=True, slots=True)
class ActiveStateView:
    session_id: SessionId | None
    actual_started_at: datetime | None
    active_deduction_kind: DeductionKind | None
    actual_deduction_started_at: datetime | None
    net_seconds: int


@dataclass(frozen=True, slots=True)
class RecoveryView:
    session_id: SessionId
    actual_started_at: datetime
    has_active_deduction: bool


@dataclass(frozen=True, slots=True)
class SleepGapView:
    session_id: SessionId
    started_at: datetime
    ended_at: datetime


@dataclass(frozen=True, slots=True)
class DaySummaryView:
    work_date: date
    first_start: datetime | None
    final_finish: datetime | None
    session_count: int
    lunch_seconds: int
    break_seconds: int
    net_seconds: int
    location: WorkLocation | None
    has_note: bool
