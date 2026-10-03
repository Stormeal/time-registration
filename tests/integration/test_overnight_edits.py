"""Overnight corrections preserve aggregate identity through durable history."""

from datetime import UTC, date, datetime

import pytest

from qi_flow.application.dto import (
    ManualDeductionCommand,
    ManualWorkSessionCommand,
    UpdateWorkSessionCommand,
)
from qi_flow.application.time_tracking import TimeTrackingApplicationService
from qi_flow.domain.errors import DomainError
from qi_flow.domain.models import DeductionKind, IsoWeek
from qi_flow.infrastructure.sqlite.database import SQLiteDatabase
from qi_flow.infrastructure.sqlite.repositories import SQLiteUnitOfWork
from qi_flow.infrastructure.system import UuidIdentifierGenerator


class Clock:
    def now(self):
        return datetime(2026, 10, 3, 12, tzinfo=UTC)


def test_overnight_correction_survives_restart_and_invalid_parent_edit_rolls_back(tmp_path):
    database = SQLiteDatabase(tmp_path / "overnight.sqlite3")
    database.initialize()

    def factory():
        return SQLiteUnitOfWork(database)

    service = TimeTrackingApplicationService(factory, Clock(), UuidIdentifierGenerator())
    session = service.add_manual_session(
        ManualWorkSessionCommand(
            datetime(2026, 10, 1, 21, tzinfo=UTC), datetime(2026, 10, 2, 1, tzinfo=UTC)
        )
    )
    deduction = service.add_manual_deduction(
        ManualDeductionCommand(
            session.id,
            DeductionKind.LUNCH,
            datetime(2026, 10, 1, 21, 45, tzinfo=UTC),
            datetime(2026, 10, 1, 22, 15, tzinfo=UTC),
        )
    )
    service.update_work_session(
        UpdateWorkSessionCommand(
            session.id,
            datetime(2026, 10, 1, 20, 30, tzinfo=UTC),
            datetime(2026, 10, 2, 1, 30, tzinfo=UTC),
        )
    )
    restarted = TimeTrackingApplicationService(factory, Clock(), UuidIdentifierGenerator())
    saved = restarted.completed_sessions()[0]
    assert saved.id == session.id
    assert restarted.completed_sessions_for_day(date(2026, 10, 1)) == [saved]
    assert restarted.completed_sessions_for_day(date(2026, 10, 2)) == [saved]
    assert restarted.completed_deductions(session.id) == [deduction]
    assert [
        day.net_seconds
        for day in restarted.summaries_for_range(date(2026, 10, 1), date(2026, 10, 3))
    ] == [4500, 11700]
    assert restarted.weekly_progress(IsoWeek(2026, 40)).logged_seconds == 16200
    history_before = restarted.entry_history_for_day(date(2026, 10, 2))
    with pytest.raises(DomainError):
        restarted.update_work_session(
            UpdateWorkSessionCommand(
                session.id, datetime(2026, 10, 1, 22, tzinfo=UTC), saved.actual_ended_at
            )
        )
    assert restarted.completed_sessions() == [saved]
    assert restarted.entry_history_for_day(date(2026, 10, 2)) == history_before
