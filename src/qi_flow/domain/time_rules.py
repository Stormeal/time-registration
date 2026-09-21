"""Pure time calculation rules used by tracking use cases."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from qi_flow.domain.errors import InvalidIntervalError
from qi_flow.domain.models import Deduction, WorkSession

COPENHAGEN = ZoneInfo("Europe/Copenhagen")
VALID_ROUNDING_MINUTES = frozenset({1, 5, 10, 15})


def round_to_nearest_interval(value: datetime, minutes: int) -> datetime:
    """Round an aware timestamp to its nearest configured minute boundary.

    Exact halfway values round upwards. Calculations happen as UTC instants, preserving
    elapsed time through Danish daylight-saving transitions.
    """
    if minutes not in VALID_ROUNDING_MINUTES:
        raise ValueError("rounding must be one of 1, 5, 10, or 15 minutes")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    interval_seconds = minutes * 60
    epoch_seconds = int(value.astimezone(UTC).timestamp())
    rounded_seconds = (
        (epoch_seconds + interval_seconds // 2) // interval_seconds
    ) * interval_seconds
    return datetime.fromtimestamp(rounded_seconds, UTC)


def round_down_to_interval(value: datetime, minutes: int) -> datetime:
    """Round an aware timestamp down to the previous configured boundary."""
    _validate_rounding(value, minutes)
    interval_seconds = minutes * 60
    epoch_seconds = int(value.astimezone(UTC).timestamp())
    return datetime.fromtimestamp((epoch_seconds // interval_seconds) * interval_seconds, UTC)


def round_up_to_interval(value: datetime, minutes: int) -> datetime:
    """Round an aware timestamp up to the next configured boundary."""
    _validate_rounding(value, minutes)
    interval_seconds = minutes * 60
    epoch_seconds = int(value.astimezone(UTC).timestamp())
    return datetime.fromtimestamp(
        ((epoch_seconds + interval_seconds - 1) // interval_seconds) * interval_seconds,
        UTC,
    )


def effective_work_interval(
    start: datetime, end: datetime, minutes: int
) -> tuple[datetime, datetime]:
    """Round timer-created work outwards so the recorded span contains the actual span."""
    rounded_start = round_down_to_interval(start, minutes)
    rounded_end = round_up_to_interval(end, minutes)
    if rounded_end <= rounded_start:
        raise InvalidIntervalError("rounding produced a zero-length interval; correct the times")
    return rounded_start, rounded_end


def effective_interval(start: datetime, end: datetime, minutes: int) -> tuple[datetime, datetime]:
    """Return rounded boundaries, rejecting an interval that rounding removes."""
    rounded_start = round_to_nearest_interval(start, minutes)
    rounded_end = round_to_nearest_interval(end, minutes)
    if rounded_end <= rounded_start:
        raise InvalidIntervalError("rounding produced a zero-length interval; correct the times")
    return rounded_start, rounded_end


def _validate_rounding(value: datetime, minutes: int) -> None:
    if minutes not in VALID_ROUNDING_MINUTES:
        raise ValueError("rounding must be one of 1, 5, 10, or 15 minutes")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")


def net_seconds(session: WorkSession, deductions: list[Deduction], now: datetime) -> int:
    """Calculate actual elapsed work after lunch/break intervals."""
    end = session.actual_ended_at or now
    gross = max(0, int((end - session.actual_started_at).total_seconds()))
    excluded = 0
    for deduction in deductions:
        if deduction.deleted_at is not None:
            continue
        deduction_end = deduction.actual_ended_at or now
        interval_start = max(deduction.actual_started_at, session.actual_started_at)
        interval_end = min(deduction_end, end)
        if interval_end > interval_start:
            excluded += int((interval_end - interval_start).total_seconds())
    return max(0, gross - excluded)


def began_on_previous_local_day(session: WorkSession, now: datetime) -> bool:
    """Identify a running session that requires explicit next-day recovery."""
    return (
        session.actual_started_at.astimezone(COPENHAGEN).date() < now.astimezone(COPENHAGEN).date()
    )


def split_at_local_midnight(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """Split an interval at Copenhagen local midnights for future timesheet allocation."""
    pieces: list[tuple[datetime, datetime]] = []
    cursor = start
    while cursor < end:
        local = cursor.astimezone(COPENHAGEN)
        next_midnight = datetime.combine(
            local.date() + timedelta(days=1), datetime.min.time(), COPENHAGEN
        )
        boundary = min(end, next_midnight.astimezone(UTC))
        pieces.append((cursor, boundary))
        cursor = boundary
    return pieces
