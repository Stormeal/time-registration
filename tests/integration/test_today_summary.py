"""Today's allocated summary uses existing rounding and Copenhagen rules."""

from datetime import UTC, datetime, timedelta

from qi_flow.application.dto import (
    FinishWorkCommand,
    ManualDeductionCommand,
    ManualWorkSessionCommand,
    StartDeductionCommand,
    StartWorkCommand,
)
from qi_flow.application.time_tracking import TimeTrackingApplicationService
from qi_flow.domain.models import DeductionKind
from qi_flow.infrastructure.sqlite.database import SQLiteDatabase
from qi_flow.infrastructure.sqlite.repositories import SQLiteUnitOfWork
from qi_flow.infrastructure.system import UuidIdentifierGenerator


class Clock:
    def __init__(self, value):
        self.value = value

    def now(self):
        return self.value


def build(tmp_path, now):
    db = SQLiteDatabase(tmp_path / "summary.sqlite3")
    db.initialize()
    clock = Clock(now)
    service = TimeTrackingApplicationService(
        lambda: SQLiteUnitOfWork(db), clock, UuidIdentifierGenerator()
    )
    return service, clock


def test_today_summary_sums_multiple_sessions_with_deductions(tmp_path):
    service, _ = build(tmp_path, datetime(2026, 9, 28, 18, tzinfo=UTC))
    session = service.add_manual_session(
        ManualWorkSessionCommand(
            datetime(2026, 9, 28, 6, tzinfo=UTC), datetime(2026, 9, 28, 14, tzinfo=UTC)
        )
    )
    service.add_manual_deduction(
        ManualDeductionCommand(
            session.id,
            DeductionKind.LUNCH,
            datetime(2026, 9, 28, 10, tzinfo=UTC),
            datetime(2026, 9, 28, 10, 30, tzinfo=UTC),
        )
    )
    service.add_manual_session(
        ManualWorkSessionCommand(
            datetime(2026, 9, 28, 17, tzinfo=UTC), datetime(2026, 9, 28, 18, tzinfo=UTC)
        )
    )
    assert hasattr(service, "today_summary")
    today = service.today_summary()
    assert (today.session_count, today.net_seconds, today.lunch_seconds) == (2, 30600, 1800)


def test_today_summary_allocates_cross_midnight_session_only_to_today(tmp_path):
    service, _ = build(tmp_path, datetime(2026, 9, 28, 0, 30, tzinfo=UTC))
    service.start_work(StartWorkCommand(datetime(2026, 9, 27, 20, 30, tzinfo=UTC)))
    assert hasattr(service, "today_summary")
    assert service.today_summary().net_seconds == 9000
    assert service.active_state().net_seconds == 14400
    assert service.today_summary().is_provisional


def test_today_summary_uses_copenhagen_day_at_dst_boundary(tmp_path):
    service, _ = build(tmp_path, datetime(2026, 10, 25, 1, 30, tzinfo=UTC))
    service.start_work(StartWorkCommand(datetime(2026, 10, 24, 22, tzinfo=UTC)))
    assert hasattr(service, "today_summary")
    assert service.today_summary().work_date.isoformat() == "2026-10-25"
    assert service.today_summary().net_seconds == 12600


def test_today_summary_keeps_existing_provisional_rounding_semantics(tmp_path):
    service, clock = build(tmp_path, datetime(2026, 9, 28, 6, 2, tzinfo=UTC))
    service.start_work(StartWorkCommand())
    clock.value += timedelta(minutes=1)
    assert hasattr(service, "today_summary")
    assert service.active_state().net_seconds == 60
    assert service.today_summary().net_seconds == 60
    service.finish_work(FinishWorkCommand())
    assert service.today_summary().net_seconds == 300
    assert not service.today_summary().is_provisional


def test_active_lunch_duration_uses_injected_clock(tmp_path):
    service, clock = build(tmp_path, datetime(2026, 9, 28, 6, tzinfo=UTC))
    service.start_work(StartWorkCommand())
    clock.value += timedelta(hours=2)
    service.start_deduction(StartDeductionCommand(DeductionKind.LUNCH))
    clock.value += timedelta(minutes=7, seconds=3)
    assert hasattr(service, "active_lunch_seconds")
    assert service.active_lunch_seconds() == 423
