"""Persistence-level coverage for local resilience and user-facing CSV files."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from qi_flow.application.dto import (
    FinishDeductionCommand,
    FinishWorkCommand,
    ManualWorkSessionCommand,
    StartDeductionCommand,
    StartWorkCommand,
)
from qi_flow.application.time_tracking import TimeTrackingApplicationService
from qi_flow.domain.models import DeductionKind, SessionId
from qi_flow.infrastructure.backups import BackupManager
from qi_flow.infrastructure.csv_export import CsvTimesheetExporter
from qi_flow.infrastructure.sqlite.database import SQLiteDatabase
from qi_flow.infrastructure.sqlite.repositories import SQLiteUnitOfWork


class FixedClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


class FixedIds:
    def __init__(self) -> None:
        self.session_count = 0
        self.deduction_count = 0

    def session_id(self) -> SessionId:
        self.session_count += 1
        return SessionId(f"session-{self.session_count}")

    def deduction_id(self) -> str:
        self.deduction_count += 1
        return f"deduction-{self.deduction_count}"

    def audit_id(self) -> str:
        return f"audit-{self.session_count}-{self.deduction_count}"


def build(
    tmp_path: Path, at: datetime
) -> tuple[TimeTrackingApplicationService, FixedClock, SQLiteDatabase]:
    database = SQLiteDatabase(tmp_path / "qi-flow.sqlite3")
    database.initialize()
    clock = FixedClock(at)
    service = TimeTrackingApplicationService(lambda: SQLiteUnitOfWork(database), clock, FixedIds())
    return service, clock, database


def test_daily_backup_keeps_active_state_and_settings_and_retains_30(tmp_path: Path) -> None:
    service, clock, database = build(tmp_path, datetime(2026, 9, 15, 7, tzinfo=UTC))
    service.set_rounding_minutes(15)
    service.start_work(StartWorkCommand())
    manager = BackupManager(
        database, tmp_path / "backups", lambda: SQLiteUnitOfWork(database), clock
    )

    first = manager.ensure_daily_backup()
    assert first is not None
    with SQLiteUnitOfWork(SQLiteDatabase(first.path)) as uow:
        assert uow.sessions.get_active() is not None
        assert uow.settings.get("rounding_minutes") == 15
    assert manager.ensure_daily_backup() is None

    for _day in range(1, 32):
        clock.value += timedelta(days=1)
        assert manager.ensure_daily_backup() is not None
    assert len(manager.list_backups()) == 30


def test_backup_failure_is_persisted_until_a_later_success(tmp_path: Path) -> None:
    _service, clock, database = build(tmp_path, datetime(2026, 9, 15, 7, tzinfo=UTC))
    blocked = tmp_path / "not-a-folder"
    blocked.write_text("blocked", encoding="utf-8")
    manager = BackupManager(database, blocked, lambda: SQLiteUnitOfWork(database), clock)

    assert manager.ensure_daily_backup() is None
    assert manager.status().warning is not None
    blocked.unlink()
    assert manager.ensure_daily_backup() is not None
    assert manager.status().warning is None


def test_restore_creates_a_safety_copy_before_replacing_live_database(tmp_path: Path) -> None:
    service, clock, database = build(tmp_path, datetime(2026, 9, 15, 17, tzinfo=UTC))
    service.add_manual_session(
        ManualWorkSessionCommand(
            datetime(2026, 9, 15, 7, tzinfo=UTC), datetime(2026, 9, 15, 8, tzinfo=UTC)
        )
    )
    manager = BackupManager(
        database, tmp_path / "backups", lambda: SQLiteUnitOfWork(database), clock
    )
    backup = manager.ensure_daily_backup()
    assert backup is not None
    service.add_manual_session(
        ManualWorkSessionCommand(
            datetime(2026, 9, 15, 9, tzinfo=UTC), datetime(2026, 9, 15, 10, tzinfo=UTC)
        )
    )

    safety = manager.restore(backup)
    assert safety.exists()
    restored_service = TimeTrackingApplicationService(
        lambda: SQLiteUnitOfWork(database), clock, FixedIds()
    )
    assert len(restored_service.completed_sessions()) == 1


def test_csv_exports_use_danish_formats_and_exclude_raw_timer_metadata(tmp_path: Path) -> None:
    service, _clock, database = build(tmp_path, datetime(2026, 9, 15, 18, tzinfo=UTC))
    service.start_work(StartWorkCommand(datetime(2026, 9, 15, 7, 2, tzinfo=UTC)))
    service.start_deduction(
        StartDeductionCommand(DeductionKind.LUNCH, datetime(2026, 9, 15, 11, 59, tzinfo=UTC))
    )
    service.finish_deduction(FinishDeductionCommand(datetime(2026, 9, 15, 12, 31, tzinfo=UTC)))
    service.finish_work(FinishWorkCommand(datetime(2026, 9, 15, 15, 2, tzinfo=UTC)))
    exporter = CsvTimesheetExporter(lambda: SQLiteUnitOfWork(database))
    summary = tmp_path / "summary.csv"
    detailed = tmp_path / "detailed.csv"

    exporter.write_summary(
        summary, service.summaries_for_range(date(2026, 9, 15), date(2026, 9, 16))
    )
    exporter.write_detailed(detailed, date(2026, 9, 15), date(2026, 9, 16))

    summary_text = summary.read_text(encoding="utf-8")
    detailed_text = detailed.read_text(encoding="utf-8")
    assert "Dato;Start;Slut" in summary_text
    assert "15/09/2026;09:00;17:05" in summary_text
    assert "7,58" in summary_text
    assert "Type;Dato;Start;Slut;Timer" in detailed_text
    assert "Arbejde;15/09/2026;09:00;17:05;8,08" in detailed_text
    assert "Frokost;15/09/2026;14:00;14:30;0,50" in detailed_text
    assert "session-1" not in detailed_text
    assert "09:02" not in detailed_text
