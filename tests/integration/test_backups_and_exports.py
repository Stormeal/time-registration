"""Persistence-level coverage for local resilience and user-facing CSV files."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from qi_flow.application.dto import (
    FinishDeductionCommand,
    FinishWorkCommand,
    ManualDeductionCommand,
    ManualWorkSessionCommand,
    StartDeductionCommand,
    StartWorkCommand,
    UpdateDayDetailsCommand,
)
from qi_flow.application.time_tracking import TimeTrackingApplicationService
from qi_flow.domain.models import DeductionKind, SessionId, WorkLocation
from qi_flow.domain.time_rules import COPENHAGEN
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


def test_new_folder_gets_todays_backup_after_old_folder_succeeded(tmp_path):
    _, clock, database = build(tmp_path, datetime(2026, 10, 3, 12, tzinfo=UTC))
    manager = BackupManager(database, tmp_path / "old", lambda: SQLiteUnitOfWork(database), clock)
    assert manager.ensure_daily_backup() is not None
    manager.set_folder(tmp_path / "new")
    backup = manager.ensure_daily_backup()
    assert backup is not None
    assert backup.path.parent == (tmp_path / "new").resolve()
    assert manager.ensure_daily_backup() is None


def test_captured_backup_completion_cannot_mark_new_folder_or_day_successful(tmp_path):
    _, clock, database = build(tmp_path, datetime(2026, 10, 3, 12, tzinfo=UTC))
    manager = BackupManager(database, tmp_path / "old", lambda: SQLiteUnitOfWork(database), clock)
    old_folder = manager.status_folder()
    manager.set_folder(tmp_path / "new")
    clock.value += timedelta(days=1)
    backup = manager.ensure_daily_backup(destination=old_folder, work_date=date(2026, 10, 3))
    assert backup is not None
    assert "2026-10-03" in backup.path.name
    assert manager.ensure_daily_backup() is not None


def test_cancelled_backup_keeps_prior_valid_copy_and_persists_no_success(tmp_path):
    _, clock, database = build(tmp_path, datetime(2026, 10, 3, 12, tzinfo=UTC))
    manager = BackupManager(
        database, tmp_path / "backups", lambda: SQLiteUnitOfWork(database), clock
    )
    first = manager.ensure_daily_backup()
    before = first.path.read_bytes()
    clock.value += timedelta(days=1)
    assert manager.ensure_daily_backup(cancelled=lambda: True) is None
    assert first.path.read_bytes() == before
    assert not list(first.path.parent.glob("*.tmp"))
    assert manager.ensure_daily_backup() is not None


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
    service.update_day_details(
        UpdateDayDetailsCommand(date(2026, 9, 15), WorkLocation.OFFICE, "Møde på Østerbro")
    )
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
    assert "Møde på Østerbro" in summary_text
    assert "Type;Dato;Start;Slut;Timer" in detailed_text
    assert "Arbejde;15/09/2026;09:00;17:05;8,08" in detailed_text
    assert "Frokost;15/09/2026;14:00;14:30;0,50" in detailed_text
    assert "session-1" not in detailed_text
    assert "09:02" not in detailed_text


@pytest.mark.parametrize(
    ("previous_day", "start_date", "end_date"),
    [
        (date(2026, 9, 30), date(2026, 10, 1), date(2026, 11, 1)),
        (date(2027, 1, 3), date(2027, 1, 4), date(2027, 1, 11)),
    ],
)
@pytest.mark.parametrize(
    "legacy_work,legacy_deductions", [(False, False), (True, True), (False, True), (True, False)]
)
def test_detailed_export_clips_cross_period_work_and_deductions(
    tmp_path: Path,
    previous_day: date,
    start_date: date,
    end_date: date,
    legacy_work: bool,
    legacy_deductions: bool,
) -> None:
    service, _clock, database = build(tmp_path, datetime(2027, 2, 1, tzinfo=UTC))
    start = datetime.combine(previous_day, datetime.min.time(), COPENHAGEN).replace(hour=23)
    session = service.add_manual_session(
        ManualWorkSessionCommand(start, start + timedelta(hours=2))
    )
    for kind, minutes_start, minutes_end in [
        (DeductionKind.LUNCH, 10, 20),
        (DeductionKind.SLEEP_BREAK, 50, 70),
    ]:
        service.add_manual_deduction(
            ManualDeductionCommand(
                session.id,
                kind,
                start + timedelta(minutes=minutes_start),
                start + timedelta(minutes=minutes_end),
            )
        )
    with SQLiteUnitOfWork(database) as uow:
        if legacy_work:
            uow.sessions.save(replace(session, effective_started_at=None, effective_ended_at=None))
        if legacy_deductions:
            for deduction in uow.deductions.list_for_session(session.id):
                uow.deductions.save(
                    replace(deduction, effective_started_at=None, effective_ended_at=None)
                )
    exporter = CsvTimesheetExporter(lambda: SQLiteUnitOfWork(database))

    rows = exporter._detailed_rows(start_date, end_date)

    assert [(row.kind, row.work_date, row.seconds) for row in rows] == [
        ("Arbejde", start_date, 3600),
        ("Pause", start_date, 600),
    ]
    summaries = service.summaries_for_range(start_date, end_date)
    assert sum(row.seconds if row.kind == "Arbejde" else -row.seconds for row in rows) == 3000
    assert sum(summary.net_seconds for summary in summaries) == 3000
    detailed = tmp_path / "selected-period.csv"
    exporter.write_detailed(detailed, start_date, end_date)
    assert detailed.read_text(encoding="utf-8").splitlines() == [
        "Type;Dato;Start;Slut;Timer",
        f"Arbejde;{start_date:%d/%m/%Y};00:00;01:00;1,00",
        f"Pause;{start_date:%d/%m/%Y};00:00;00:10;0,17",
    ]


@pytest.mark.parametrize(
    ("work_date", "expected_work_seconds"),
    [(date(2026, 3, 29), 82800), (date(2026, 10, 25), 90000)],
)
def test_detailed_export_splits_dst_midnights_and_matches_each_daily_summary(
    tmp_path: Path, work_date: date, expected_work_seconds: int
) -> None:
    service, _clock, database = build(tmp_path, datetime(2026, 11, 1, tzinfo=UTC))
    first_day = work_date - timedelta(days=1)
    last_day = work_date + timedelta(days=1)
    start = datetime.combine(first_day, datetime.min.time(), COPENHAGEN).replace(hour=23)
    end = datetime.combine(last_day, datetime.min.time(), COPENHAGEN).replace(hour=1)
    session = service.add_manual_session(ManualWorkSessionCommand(start, end))
    for kind, deduction_start, deduction_end in [
        (DeductionKind.LUNCH, start + timedelta(minutes=30), start + timedelta(minutes=90)),
        (DeductionKind.SLEEP_BREAK, end - timedelta(minutes=90), end - timedelta(minutes=30)),
    ]:
        service.add_manual_deduction(
            ManualDeductionCommand(session.id, kind, deduction_start, deduction_end)
        )
    exporter = CsvTimesheetExporter(lambda: SQLiteUnitOfWork(database))

    rows = exporter._detailed_rows(first_day, last_day + timedelta(days=1))

    assert [(row.work_date, row.seconds) for row in rows if row.kind == "Arbejde"] == [
        (first_day, 3600),
        (work_date, expected_work_seconds),
        (last_day, 3600),
    ]
    for summary in service.summaries_for_range(first_day, last_day + timedelta(days=1)):
        daily_rows = [row for row in rows if row.work_date == summary.work_date]
        assert (
            sum(row.seconds if row.kind == "Arbejde" else -row.seconds for row in daily_rows)
            == summary.net_seconds
        )
    assert sum(row.seconds for row in rows if row.kind != "Arbejde") == 7200
    assert sum(row.seconds if row.kind == "Arbejde" else -row.seconds for row in rows) == (
        expected_work_seconds
    )
    selected_rows = exporter._detailed_rows(work_date, last_day)
    assert [(row.kind, row.seconds) for row in selected_rows] == [
        ("Arbejde", expected_work_seconds),
        ("Frokost", 1800),
        ("Pause", 1800),
    ]


def test_detailed_export_clips_legacy_effective_deductions_to_parent(tmp_path: Path) -> None:
    service, _clock, database = build(tmp_path, datetime(2026, 10, 2, tzinfo=UTC))
    start = datetime(2026, 10, 1, 8, tzinfo=UTC)
    session = service.add_manual_session(
        ManualWorkSessionCommand(start, start + timedelta(hours=2))
    )
    deduction = service.add_manual_deduction(
        ManualDeductionCommand(
            session.id, DeductionKind.LUNCH, start, start + timedelta(minutes=30)
        )
    )
    with SQLiteUnitOfWork(database) as uow:
        uow.deductions.save(replace(deduction, effective_started_at=start - timedelta(hours=1)))
    exporter = CsvTimesheetExporter(lambda: SQLiteUnitOfWork(database))

    rows = exporter._detailed_rows(date(2026, 10, 1), date(2026, 10, 2))

    assert [(row.kind, row.seconds) for row in rows] == [("Arbejde", 7200), ("Frokost", 1800)]
    assert service.summaries_for_range(date(2026, 10, 1), date(2026, 10, 2))[0].net_seconds == 5400


def test_detailed_export_excludes_deleted_work_and_deductions(tmp_path: Path) -> None:
    service, clock, database = build(tmp_path, datetime(2026, 10, 2, tzinfo=UTC))
    start = datetime(2026, 10, 1, 8, tzinfo=UTC)
    session = service.add_manual_session(
        ManualWorkSessionCommand(start, start + timedelta(hours=2))
    )
    deleted_work = service.add_manual_session(
        ManualWorkSessionCommand(start + timedelta(hours=3), start + timedelta(hours=4))
    )
    deleted_deduction = service.add_manual_deduction(
        ManualDeductionCommand(
            session.id, DeductionKind.SLEEP_BREAK, start, start + timedelta(minutes=30)
        )
    )
    with SQLiteUnitOfWork(database) as uow:
        uow.sessions.save(replace(deleted_work, deleted_at=clock.now()))
        uow.deductions.save(replace(deleted_deduction, deleted_at=clock.now()))
    exporter = CsvTimesheetExporter(lambda: SQLiteUnitOfWork(database))
    detailed = tmp_path / "detailed.csv"

    exporter.write_detailed(detailed, date(2026, 10, 1), date(2026, 10, 2))

    assert detailed.read_text(encoding="utf-8").splitlines() == [
        "Type;Dato;Start;Slut;Timer",
        "Arbejde;01/10/2026;10:00;12:00;2,00",
    ]


@pytest.mark.parametrize(
    "legacy_work,legacy_deduction", [(True, True), (False, True), (True, False)]
)
def test_detailed_export_preserves_completed_legacy_intervals_without_effective_times(
    tmp_path: Path, legacy_work: bool, legacy_deduction: bool
) -> None:
    service, _clock, database = build(tmp_path, datetime(2026, 10, 2, tzinfo=UTC))
    start = datetime(2026, 10, 1, 8, tzinfo=UTC)
    session = service.add_manual_session(
        ManualWorkSessionCommand(start, start + timedelta(hours=2))
    )
    deduction = service.add_manual_deduction(
        ManualDeductionCommand(
            session.id,
            DeductionKind.LUNCH,
            start + timedelta(minutes=30),
            start + timedelta(hours=1),
        )
    )
    with SQLiteUnitOfWork(database) as uow:
        if legacy_work:
            uow.sessions.save(replace(session, effective_started_at=None, effective_ended_at=None))
        if legacy_deduction:
            uow.deductions.save(
                replace(deduction, effective_started_at=None, effective_ended_at=None)
            )
    exporter = CsvTimesheetExporter(lambda: SQLiteUnitOfWork(database))

    rows = exporter._detailed_rows(date(2026, 10, 1), date(2026, 10, 2))

    assert [(row.kind, row.seconds) for row in rows] == [("Arbejde", 7200), ("Frokost", 1800)]
    net_exported = sum(row.seconds if row.kind == "Arbejde" else -row.seconds for row in rows)
    summary = service.summaries_for_range(date(2026, 10, 1), date(2026, 10, 2))[0]
    assert net_exported == summary.net_seconds == 5400
    detailed = tmp_path / "legacy.csv"
    exporter.write_detailed(detailed, date(2026, 10, 1), date(2026, 10, 2))
    assert detailed.read_text(encoding="utf-8").splitlines() == [
        "Type;Dato;Start;Slut;Timer",
        "Arbejde;01/10/2026;10:00;12:00;2,00",
        "Frokost;01/10/2026;10:30;11:00;0,50",
    ]


def test_detailed_export_omits_active_work_and_lunch(tmp_path: Path) -> None:
    service, _clock, database = build(tmp_path, datetime(2026, 10, 1, 12, tzinfo=UTC))
    service.start_work(StartWorkCommand(datetime(2026, 10, 1, 8, tzinfo=UTC)))
    service.start_deduction(
        StartDeductionCommand(DeductionKind.LUNCH, datetime(2026, 10, 1, 11, tzinfo=UTC))
    )
    exporter = CsvTimesheetExporter(lambda: SQLiteUnitOfWork(database))

    assert exporter._detailed_rows(date(2026, 10, 1), date(2026, 10, 2)) == []


def test_detailed_export_omits_open_legacy_deduction_on_completed_work(tmp_path: Path) -> None:
    service, _clock, database = build(tmp_path, datetime(2026, 10, 2, tzinfo=UTC))
    start = datetime(2026, 10, 1, 8, tzinfo=UTC)
    session = service.add_manual_session(
        ManualWorkSessionCommand(start, start + timedelta(hours=2))
    )
    deduction = service.add_manual_deduction(
        ManualDeductionCommand(
            session.id,
            DeductionKind.LUNCH,
            start + timedelta(minutes=30),
            start + timedelta(hours=1),
        )
    )
    with SQLiteUnitOfWork(database) as uow:
        uow.deductions.save(
            replace(
                deduction, actual_ended_at=None, effective_started_at=None, effective_ended_at=None
            )
        )
    exporter = CsvTimesheetExporter(lambda: SQLiteUnitOfWork(database))

    rows = exporter._detailed_rows(date(2026, 10, 1), date(2026, 10, 2))

    assert [(row.kind, row.seconds) for row in rows] == [("Arbejde", 7200)]
