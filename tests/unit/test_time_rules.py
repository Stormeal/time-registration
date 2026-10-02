"""Timer rounding rules at real Copenhagen wall times."""

from datetime import datetime

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
