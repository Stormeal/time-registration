"""Real migrations and transactions around Testhuset planning and reconciliation."""

from contextlib import closing
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from qi_flow.application.dsb import DsbService
from qi_flow.application.dto import (
    ManualDeductionCommand,
    ManualWorkSessionCommand,
    StartWorkCommand,
)
from qi_flow.application.testhuset import HourSlot, TesthusetService
from qi_flow.application.time_tracking import TimeTrackingApplicationService
from qi_flow.domain.models import DeductionKind, IsoWeek
from qi_flow.domain.testhuset import ProjectTask, decimal_hours, parse_hours
from qi_flow.infrastructure.sqlite.database import SQLiteDatabase
from qi_flow.infrastructure.sqlite.repositories import SQLiteUnitOfWork
from qi_flow.infrastructure.system import UuidIdentifierGenerator
from qi_flow.infrastructure.testhuset_cache import JsonTaskCache

TASK = ProjectTask("11-22", "Example project", "Testing")
OTHER = ProjectTask("33-44", "Other project", "Development")
WEEK = IsoWeek(2026, 38)


class Clock:
    def now(self) -> datetime:
        return datetime(2026, 11, 1, tzinfo=UTC)


class Sheet:
    def __init__(self) -> None:
        self.tasks = (TASK, OTHER)
        self.values: dict[tuple[date, str], str] = {}
        self.writes: list[HourSlot] = []
        self.weeks: list[IsoWeek] = []
        self.fail = False

    def scan(self, week: IsoWeek) -> tuple[ProjectTask, ...]:
        self.weeks.append(week)
        return self.tasks

    def read(self, slot: HourSlot) -> str:
        return self.values.get((slot.work_date, slot.task.id), "")

    def write_verified(self, slot: HourSlot) -> None:
        if self.fail:
            raise ValueError("Uncertain save")
        self.writes.append(slot)
        self.values[(slot.work_date, slot.task.id)] = slot.hours


class DsbSheet(Sheet):
    def __init__(self) -> None:
        super().__init__()
        self.commits = 0

    def commit_verified(self) -> None:
        self.commits += 1


@pytest.fixture
def setup(tmp_path: Path):
    database = SQLiteDatabase(tmp_path / "time.sqlite3")
    database.initialize()
    cache = JsonTaskCache(tmp_path / "testhuset-projects.json")
    ids = UuidIdentifierGenerator()
    tracking = TimeTrackingApplicationService(lambda: SQLiteUnitOfWork(database), Clock(), ids)
    service = TesthusetService(lambda: SQLiteUnitOfWork(database), Clock(), ids, cache)
    sheet = Sheet()
    service.scan(sheet, WEEK)
    service.set_default(TASK.id)
    return tracking, service, sheet, database, cache


def stamp(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=UTC)


@pytest.mark.parametrize("value", ["7.750", "7,750", "1,234.50", "NaN", "-1", "7:45"])
def test_ambiguous_hours_are_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        parse_hours(value)


def test_decimal_conversion_and_comma_input() -> None:
    assert decimal_hours(7 * 3600 + 45 * 60) == "7.75"
    assert decimal_hours(18) == "0.01"
    assert parse_hours("7,75") == parse_hours("7.75")
    assert parse_hours("") == 0
    assert parse_hours("25.00") == 25  # A Copenhagen autumn clock-change day has 25 hours.


def test_scan_replaces_tasks_without_hours_or_secrets(setup) -> None:
    _, service, sheet, _, cache = setup
    sheet.tasks = (OTHER,)
    service.scan(sheet, WEEK)
    assert service.tasks() == (OTHER,)
    assert not sheet.writes
    assert set(__import__("json").loads(cache.path.read_text())[0]) == {
        "id",
        "project_name",
        "task_name",
    }
    with pytest.raises(ValueError, match="default"):
        service.proposed_slots(WEEK)


def test_assignment_persists_and_can_be_restored_without_changing_times(setup) -> None:
    tracking, service, _, database, _ = setup
    session = tracking.add_manual_session(ManualWorkSessionCommand(stamp(14, 7), stamp(14, 15)))
    service.assign(session.id, OTHER.id)
    with SQLiteUnitOfWork(database) as uow:
        saved = uow.sessions.get(session.id)
        assert saved.testhuset_task_id == OTHER.id
        assert saved.actual_started_at == stamp(14, 7)
    assert service.proposed_slots(WEEK)[0].task == OTHER
    assert tracking.restore_work_session(session.id).testhuset_task_id is None
    assert service.proposed_slots(WEEK)[0].task == TASK


def test_net_time_is_aggregated_per_task_before_decimal_rounding(setup) -> None:
    tracking, service, _, _, _ = setup
    session = tracking.add_manual_session(ManualWorkSessionCommand(stamp(14, 7), stamp(14, 15)))
    tracking.add_manual_deduction(
        ManualDeductionCommand(session.id, DeductionKind.LUNCH, stamp(14, 11), stamp(14, 11, 15))
    )
    tracking.add_manual_session(ManualWorkSessionCommand(stamp(14, 16), stamp(14, 16, 1)))
    tracking.add_manual_session(ManualWorkSessionCommand(stamp(14, 17), stamp(14, 17, 1)))
    slots = service.proposed_slots(WEEK)
    assert len(slots) == 1
    assert slots[0].hours == "7.78"


def test_midnight_and_dst_are_allocated_by_copenhagen_date(setup) -> None:
    tracking, service, _, _, _ = setup
    tracking.add_manual_session(ManualWorkSessionCommand(stamp(20, 21), stamp(21, 1)))
    assert service.proposed_slots(WEEK)[0].hours == "1.00"
    assert service.proposed_slots(IsoWeek(2026, 39))[0].hours == "3.00"
    tracking.add_manual_session(
        ManualWorkSessionCommand(
            datetime(2026, 10, 24, 22, tzinfo=UTC), datetime(2026, 10, 25, 3, tzinfo=UTC)
        )
    )
    assert service.proposed_slots(IsoWeek(2026, 43))[0].hours == "5.00"


def test_removed_override_blocks_preview_but_active_session_is_excluded(setup) -> None:
    tracking, service, sheet, _, _ = setup
    session = tracking.add_manual_session(ManualWorkSessionCommand(stamp(14, 7), stamp(14, 15)))
    service.assign(session.id, OTHER.id)
    sheet.tasks = (TASK,)
    with pytest.raises(ValueError, match="removed task"):
        service.preview(sheet, WEEK)
    service.assign(session.id, None)
    tracking.start_work(StartWorkCommand(stamp(15, 7)))
    preview = service.preview(sheet, WEEK)
    assert [item.proposed.work_date for item in preview.slots] == [date(2026, 9, 14)]
    result = service.fill(sheet, preview, frozenset({0}), confirmed=True)
    assert result.changed == 1
    assert [slot.work_date for slot in sheet.writes] == [date(2026, 9, 14)]


def test_confirmation_matching_keep_replace_and_retry(setup) -> None:
    tracking, service, sheet, _, _ = setup
    for day in (14, 15, 16):
        tracking.add_manual_session(ManualWorkSessionCommand(stamp(day, 7), stamp(day, 15)))
    sheet.values[(date(2026, 9, 14), TASK.id)] = "8,00"
    sheet.values[(date(2026, 9, 15), TASK.id)] = "3,50"
    preview = service.preview(sheet, WEEK)
    assert not sheet.writes
    with pytest.raises(ValueError, match="Confirm"):
        service.fill(sheet, preview, frozenset({2}), confirmed=False)
    result = service.fill(sheet, preview, frozenset({2}), confirmed=True)
    assert (result.changed, result.kept, result.matched) == (1, 1, 1)
    assert sheet.writes[0].hours == "8.00"
    assert sheet.values[(date(2026, 9, 15), TASK.id)] == "3,50"
    retry = service.preview(sheet, WEEK)
    assert service.fill(sheet, retry, frozenset({2}), confirmed=True).changed == 0


@pytest.mark.parametrize("change", ["remote", "local", "default"])
def test_stale_preview_never_writes(setup, change: str) -> None:
    tracking, service, sheet, _, _ = setup
    tracking.add_manual_session(ManualWorkSessionCommand(stamp(14, 7), stamp(14, 15)))
    preview = service.preview(sheet, WEEK)
    if change == "remote":
        sheet.values[(date(2026, 9, 14), TASK.id)] = "2.00"
    elif change == "local":
        tracking.add_manual_session(ManualWorkSessionCommand(stamp(15, 7), stamp(15, 15)))
    else:
        service.set_default(OTHER.id)
    with pytest.raises(ValueError, match="changed"):
        service.fill(sheet, preview, frozenset({0}), confirmed=True)
    assert not sheet.writes


def test_uncertain_save_stops_without_retry(setup) -> None:
    tracking, service, sheet, _, _ = setup
    tracking.add_manual_session(ManualWorkSessionCommand(stamp(14, 7), stamp(14, 15)))
    preview = service.preview(sheet, WEEK)
    sheet.fail = True
    with pytest.raises(ValueError, match="Uncertain"):
        service.fill(sheet, preview, frozenset({0}), confirmed=True)
    assert not sheet.writes


def test_dsb_fill_sends_only_a_changed_reviewed_batch(setup) -> None:
    tracking, _, _, database, cache = setup
    ids = UuidIdentifierGenerator()
    dsb = DsbService(lambda: SQLiteUnitOfWork(database), Clock(), ids, cache)
    sheet = DsbSheet()
    dsb.scan(sheet, WEEK)
    dsb.set_default(TASK.id)
    tracking.add_manual_session(ManualWorkSessionCommand(stamp(14, 7), stamp(14, 15)))

    preview = dsb.preview(sheet, WEEK)
    result = dsb.fill(sheet, preview, frozenset({0}), confirmed=True)

    assert result.changed == 1
    assert len(sheet.writes) == 1
    assert sheet.commits == 1
    assert sheet.weeks == [WEEK]


def test_dsb_preview_uses_the_cached_allocation_without_rescanning(setup) -> None:
    tracking, _, _, database, cache = setup
    ids = UuidIdentifierGenerator()
    dsb = DsbService(lambda: SQLiteUnitOfWork(database), Clock(), ids, cache)
    sheet = DsbSheet()
    dsb.scan(sheet, WEEK)
    dsb.set_default(TASK.id)
    tracking.add_manual_session(ManualWorkSessionCommand(stamp(14, 7), stamp(14, 15)))
    sheet.weeks.clear()

    preview = dsb.preview(sheet, WEEK)

    assert len(preview.slots) == 1
    assert sheet.weeks == []


def test_upgrade_preserves_existing_sessions(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "old.sqlite3")
    migrations = Path("src/qi_flow/infrastructure/sqlite/migrations")
    with closing(database.connect()) as connection:
        connection.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, name TEXT)")
        for version in (1, 2, 3):
            path = next(migrations.glob(f"{version:04}_*.sql"))
            connection.executescript(path.read_text())
            connection.execute("INSERT INTO schema_migrations VALUES (?, ?)", (version, path.name))
        connection.execute(
            "INSERT INTO work_sessions(id, actual_started_at_utc, source, created_at_utc, "
            "updated_at_utc) VALUES ('existing', ?, 'timer', ?, ?)",
            (stamp(14, 7).isoformat(),) * 3,
        )
        connection.commit()
    database.initialize()
    with SQLiteUnitOfWork(database) as uow:
        saved = uow.sessions.get_active()
        assert saved.id == "existing"
        assert saved.testhuset_task_id is None
