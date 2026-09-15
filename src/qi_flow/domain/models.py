"""Core entities shared by application use cases."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import NewType

from qi_flow.domain.errors import InvalidIntervalError

SessionId = NewType("SessionId", str)
DeductionId = NewType("DeductionId", str)


class EntrySource(StrEnum):
    """How an interval entered the system."""

    TIMER = "timer"
    MANUAL = "manual"
    RECOVERY = "recovery"


class DeductionKind(StrEnum):
    """Time excluded from the net duration of a work session."""

    LUNCH = "lunch"
    SLEEP_BREAK = "sleep_break"


class WorkLocation(StrEnum):
    """Daily workplace context."""

    REMOTE = "remote"
    OFFICE = "office"


def _require_aware(value: datetime | None, field_name: str) -> None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError(f"{field_name} must be timezone-aware")


@dataclass(slots=True)
class WorkSession:
    """A continuous gross work span with optional rounded effective bounds."""

    id: SessionId
    actual_started_at: datetime
    actual_ended_at: datetime | None = None
    effective_started_at: datetime | None = None
    effective_ended_at: datetime | None = None
    source: EntrySource = EntrySource.TIMER
    created_at: datetime | None = None
    updated_at: datetime | None = None
    deleted_at: datetime | None = None
    recovery_acknowledged_at: datetime | None = None
    rounding_minutes: int = 5
    revision: int = 1

    def __post_init__(self) -> None:
        for field_name in (
            "actual_started_at",
            "actual_ended_at",
            "effective_started_at",
            "effective_ended_at",
            "created_at",
            "updated_at",
            "deleted_at",
            "recovery_acknowledged_at",
        ):
            _require_aware(getattr(self, field_name), field_name)
        if self.actual_ended_at is not None and self.actual_ended_at <= self.actual_started_at:
            raise InvalidIntervalError("work-session end must be after its start")
        if self.rounding_minutes not in {1, 5, 10, 15}:
            raise ValueError("rounding must be one of 1, 5, 10, or 15 minutes")
        if (self.effective_started_at is None) != (self.effective_ended_at is None):
            raise InvalidIntervalError(
                "effective session boundaries must be both set or both absent"
            )
        if (
            self.effective_started_at is not None
            and self.effective_ended_at is not None
            and self.effective_ended_at <= self.effective_started_at
        ):
            raise InvalidIntervalError("effective work-session end must be after its start")

    @property
    def is_active(self) -> bool:
        """Return whether the work session has no actual end."""
        return self.actual_ended_at is None and self.deleted_at is None


@dataclass(slots=True)
class Deduction:
    """A lunch or excluded sleep interval contained by a work session."""

    id: DeductionId
    session_id: SessionId
    kind: DeductionKind
    actual_started_at: datetime
    actual_ended_at: datetime | None = None
    effective_started_at: datetime | None = None
    effective_ended_at: datetime | None = None
    source: EntrySource = EntrySource.TIMER
    created_at: datetime | None = None
    updated_at: datetime | None = None
    deleted_at: datetime | None = None
    revision: int = 1
    rounding_minutes: int = 5

    def __post_init__(self) -> None:
        for field_name in (
            "actual_started_at",
            "actual_ended_at",
            "effective_started_at",
            "effective_ended_at",
            "created_at",
            "updated_at",
            "deleted_at",
        ):
            _require_aware(getattr(self, field_name), field_name)
        if self.actual_ended_at is not None and self.actual_ended_at <= self.actual_started_at:
            raise InvalidIntervalError("deduction end must be after its start")
        if self.rounding_minutes not in {1, 5, 10, 15}:
            raise ValueError("rounding must be one of 1, 5, 10, or 15 minutes")
        if (self.effective_started_at is None) != (self.effective_ended_at is None):
            raise InvalidIntervalError(
                "effective deduction boundaries must be both set or both absent"
            )

    @property
    def is_active(self) -> bool:
        """Return whether the deduction has no actual end."""
        return self.actual_ended_at is None and self.deleted_at is None


@dataclass(slots=True)
class DayDetails:
    """Context attached to a local calendar date."""

    work_date: date
    location: WorkLocation = WorkLocation.REMOTE
    note: str = ""
    revision: int = 1


@dataclass(frozen=True, slots=True)
class IsoWeek:
    """An ISO week identity independent of locale formatting."""

    year: int
    week: int

    def __post_init__(self) -> None:
        if not 1 <= self.week <= 53:
            raise ValueError("ISO week must be between 1 and 53")


@dataclass(slots=True)
class WeeklyTarget:
    """A target override for one ISO week."""

    iso_week: IsoWeek
    target_minutes: int = field(default=37 * 60)

    def __post_init__(self) -> None:
        if self.target_minutes < 0:
            raise ValueError("weekly target cannot be negative")
