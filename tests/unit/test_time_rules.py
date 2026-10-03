"""Timer rounding rules at real Copenhagen wall times."""

from datetime import UTC, date, datetime

import pytest

from qi_flow.domain import time_rules
from qi_flow.domain.time_rules import COPENHAGEN, effective_work_interval


def test_timer_start_uses_previous_boundary_only_when_it_is_closer() -> None:
    end = datetime(2026, 10, 2, 9, 0, tzinfo=COPENHAGEN)

    early, _ = effective_work_interval(datetime(2026, 10, 2, 8, 6, tzinfo=COPENHAGEN), end, 15)
    late, _ = effective_work_interval(datetime(2026, 10, 2, 8, 40, tzinfo=COPENHAGEN), end, 15)

    assert early.astimezone(COPENHAGEN).strftime("%H:%M") == "08:00"
    assert late.astimezone(COPENHAGEN).strftime("%H:%M") == "08:40"


def test_timer_start_at_halfway_stays_at_actual_time() -> None:
    start = datetime(2026, 10, 2, 8, 7, 30, tzinfo=COPENHAGEN)
    end = datetime(2026, 10, 2, 9, 0, tzinfo=COPENHAGEN)

    effective_start, _ = effective_work_interval(start, end, 15)

    assert effective_start == start


@pytest.mark.parametrize(
    ("work_date", "expected_start", "expected_end", "hours"),
    [
        (
            date(2026, 3, 29),
            datetime(2026, 3, 28, 23, tzinfo=UTC),
            datetime(2026, 3, 29, 22, tzinfo=UTC),
            23,
        ),
        (
            date(2026, 10, 25),
            datetime(2026, 10, 24, 22, tzinfo=UTC),
            datetime(2026, 10, 25, 23, tzinfo=UTC),
            25,
        ),
    ],
)
def test_local_day_bounds_follow_next_calendar_midnight(
    work_date: date, expected_start: datetime, expected_end: datetime, hours: int
) -> None:
    start, end = time_rules.local_day_bounds(work_date)

    assert (start, end) == (expected_start, expected_end)
    assert start.tzinfo is UTC and end.tzinfo is UTC
    assert (end - start).total_seconds() == hours * 3600
