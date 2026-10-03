"""Validate live work and deduction aggregates independently of persistence."""

from collections.abc import Sequence
from datetime import UTC, datetime

from qi_flow.domain.errors import (
    InvalidIntervalError,
    InvalidStateTransitionError,
    OverlappingIntervalError,
)
from qi_flow.domain.models import Deduction, WorkSession


def validate_intervals(
    sessions: Sequence[WorkSession], deductions: Sequence[Deduction], *, as_of: datetime
) -> None:
    """Reject invalid live aggregates; open intervals end at the supplied instant.

    Overlap and containment use actual instants, matching manual correction rules.
    Effective boundaries are checked for consistency and positive duration, but their
    rounding may extend beyond actual boundaries (including beyond ``as_of``).
    """
    now = _instant(as_of)
    live_sessions = [session for session in sessions if session.deleted_at is None]
    live_deductions = [deduction for deduction in deductions if deduction.deleted_at is None]
    if len({session.id for session in live_sessions}) != len(live_sessions):
        raise InvalidIntervalError("Work-session identities must be unique.")
    if len({deduction.id for deduction in live_deductions}) != len(live_deductions):
        raise InvalidIntervalError("Lunch and break identities must be unique.")
    if sum(session.is_active for session in live_sessions) > 1:
        raise InvalidStateTransitionError("Only one work session can be running.")
    session_bounds = {session.id: _bounds(session, now) for session in live_sessions}
    _ensure_no_overlap(list(session_bounds.values()), "Work sessions cannot overlap.")
    parents = {session.id: session for session in live_sessions}
    deduction_bounds: dict[str, list[tuple[datetime, datetime]]] = {}
    if sum(deduction.is_active for deduction in live_deductions) > 1:
        raise InvalidStateTransitionError("Only one lunch or break can be running.")
    for deduction in live_deductions:
        parent = parents.get(deduction.session_id)
        if parent is None:
            raise InvalidStateTransitionError("Restore the parent work session first.")
        if deduction.is_active and not parent.is_active:
            raise InvalidStateTransitionError("A running lunch or break needs running work.")
        start, end = _bounds(deduction, now)
        parent_start, parent_end = session_bounds[parent.id]
        if start < parent_start or end > parent_end:
            raise InvalidIntervalError("Lunches and breaks must stay inside their work session.")
        deduction_bounds.setdefault(parent.id, []).append((start, end))
    for bounds in deduction_bounds.values():
        _ensure_no_overlap(bounds, "Lunches and breaks cannot overlap.")


def _instant(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidIntervalError("Interval timestamps must be timezone-aware.")
    return value.astimezone(UTC)


def _bounds(entry: WorkSession | Deduction, now: datetime) -> tuple[datetime, datetime]:
    if entry.rounding_minutes not in {1, 5, 10, 15}:
        raise InvalidIntervalError("Rounding must be one of 1, 5, 10, or 15 minutes.")
    start = _instant(entry.actual_started_at)
    end = _instant(entry.actual_ended_at) if entry.actual_ended_at is not None else now
    if start > now or end > now:
        raise InvalidIntervalError("Future time entries are not allowed.")
    if end < start or (entry.actual_ended_at is not None and end == start):
        raise InvalidIntervalError("End time must be after start time.")
    effective_start, effective_end = entry.effective_started_at, entry.effective_ended_at
    if (effective_start is None) != (effective_end is None):
        raise InvalidIntervalError("Effective boundaries must be both set or both absent.")
    if effective_start is not None and effective_end is not None:
        if entry.actual_ended_at is None:
            raise InvalidIntervalError("Running intervals cannot have completed effective bounds.")
        if _instant(effective_end) <= _instant(effective_start):
            raise InvalidIntervalError("Effective end time must be after start time.")
    return start, end


def _ensure_no_overlap(bounds: list[tuple[datetime, datetime]], message: str) -> None:
    latest_end: datetime | None = None
    for start, end in sorted(bounds):
        if latest_end is not None and start < latest_end:
            raise OverlappingIntervalError(message)
        latest_end = end
