"""Task configuration and explicit, reconciled weekly fills without Qt or browser imports."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Protocol

from qi_flow.application.ports import Clock, IdentifierGenerator, UnitOfWork
from qi_flow.application.time_tracking import TimeTrackingApplicationService
from qi_flow.domain.models import IsoWeek, SessionId
from qi_flow.domain.testhuset import ProjectTask, decimal_hours, parse_hours
from qi_flow.domain.time_rules import COPENHAGEN, split_at_local_midnight


class TaskCache(Protocol):
    def load(self) -> tuple[ProjectTask, ...]: ...

    def replace(self, tasks: tuple[ProjectTask, ...]) -> None: ...


@dataclass(frozen=True, slots=True)
class HourSlot:
    work_date: date
    task: ProjectTask
    hours: str


@dataclass(frozen=True, slots=True)
class PreviewSlot:
    proposed: HourSlot
    existing: str

    @property
    def matches(self) -> bool:
        return parse_hours(self.existing) == parse_hours(self.proposed.hours)


@dataclass(frozen=True, slots=True)
class FillPreview:
    week: IsoWeek
    slots: tuple[PreviewSlot, ...]


@dataclass(frozen=True, slots=True)
class FillResult:
    changed: int
    kept: int
    matched: int


class WeeklySheet(Protocol):
    """A temporary signed-in browser session. No close-week operation exists."""

    def scan(self, week: IsoWeek) -> tuple[ProjectTask, ...]: ...

    def read(self, slot: HourSlot) -> str: ...

    def write_verified(self, slot: HourSlot) -> None: ...


@dataclass(frozen=True, slots=True)
class TesthusetCredential:
    __test__ = False
    username: str
    password: str


class TesthusetCredentialStore(Protocol):
    def load(self) -> TesthusetCredential | None: ...

    def save(self, credential: TesthusetCredential) -> None: ...

    def clear(self) -> None: ...


class TesthusetService:
    __test__ = False

    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        clock: Clock,
        identifiers: IdentifierGenerator,
        cache: TaskCache,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._identifiers = identifiers
        self._cache = cache

    def tasks(self) -> tuple[ProjectTask, ...]:
        return self._cache.load()

    def default_task_id(self) -> str | None:
        with self._uow_factory() as uow:
            value = uow.settings.get("testhuset_default_task")
        return value if isinstance(value, str) else None

    def set_default(self, task_id: str) -> None:
        self._require_task(task_id)
        with self._uow_factory() as uow:
            uow.settings.save("testhuset_default_task", task_id, self._clock.now())

    def assign(self, session_id: SessionId, task_id: str | None) -> None:
        if task_id is not None:
            self._require_task(task_id)
        with self._uow_factory() as uow:
            session = uow.sessions.get(session_id)
            if session is None or session.deleted_at is not None or session.is_active:
                raise ValueError("Choose a completed work session before assigning a task.")
            if session.testhuset_task_id == task_id:
                return
            now = self._clock.now()
            uow.audit.record(
                self._identifiers.audit_id(),
                "work_session",
                str(session.id),
                "update",
                TimeTrackingApplicationService._session_snapshot(session),
                now,
            )
            session.testhuset_task_id = task_id
            session.updated_at = now
            session.revision += 1
            uow.sessions.save(session)

    def _require_task(self, task_id: str) -> None:
        if task_id not in {task.id for task in self.tasks()}:
            raise ValueError("Choose a current Testhuset task from the latest scan.")

    def scan(self, sheet: WeeklySheet, week: IsoWeek) -> tuple[ProjectTask, ...]:
        tasks = sheet.scan(week)
        if len({task.id for task in tasks}) != len(tasks):
            raise ValueError("Testhuset returned duplicate task identifiers; scan cancelled.")
        self._cache.replace(tasks)
        return tasks

    def proposed_slots(self, week: IsoWeek) -> tuple[HourSlot, ...]:
        monday = date.fromisocalendar(week.year, week.week, 1)
        start = datetime.combine(monday, datetime.min.time(), COPENHAGEN).astimezone(UTC)
        end = datetime.combine(monday + timedelta(days=7), datetime.min.time(), COPENHAGEN)
        end = end.astimezone(UTC)
        tasks = {task.id: task for task in self.tasks()}
        default = self.default_task_id()
        if default not in tasks:
            raise ValueError("Select a current default Testhuset task in Settings before filling.")
        totals: dict[tuple[date, str], int] = {}
        with self._uow_factory() as uow:
            # Include the rounding margin at week boundaries, then clip effective intervals.
            for session in uow.sessions.list_intersecting(
                start - timedelta(minutes=15), end + timedelta(minutes=15)
            ):
                left = session.effective_started_at or session.actual_started_at
                right = session.effective_ended_at or session.actual_ended_at
                # A running session has no stable net duration. It must not block
                # registration of already completed sessions from earlier days.
                if right is None:
                    continue
                if left >= end or right <= start:
                    continue
                task_id = session.testhuset_task_id or default
                if task_id not in tasks:
                    raise ValueError("A session uses a removed task. Choose a current override.")
                deductions = uow.deductions.list_for_session(session.id)
                for a, b in split_at_local_midnight(max(left, start), min(right, end)):
                    seconds = int((b - a).total_seconds())
                    for deduction in deductions:
                        if deduction.deleted_at is not None:
                            continue
                        dstart = deduction.effective_started_at or deduction.actual_started_at
                        dend = deduction.effective_ended_at or deduction.actual_ended_at
                        if dend is None:
                            raise ValueError("Resolve unfinished deductions before filling.")
                        seconds -= max(0, int((min(b, dend) - max(a, dstart)).total_seconds()))
                    key = (a.astimezone(COPENHAGEN).date(), task_id)
                    totals[key] = totals.get(key, 0) + seconds
        return tuple(
            HourSlot(day, tasks[task_id], decimal_hours(seconds))
            for (day, task_id), seconds in sorted(totals.items())
        )

    def preview(self, sheet: WeeklySheet, week: IsoWeek) -> FillPreview:
        self.scan(sheet, week)
        slots = tuple(PreviewSlot(slot, sheet.read(slot)) for slot in self.proposed_slots(week))
        for slot in slots:
            parse_hours(slot.existing)
            parse_hours(slot.proposed.hours)
        if not slots:
            raise ValueError("There is no completed work in the selected week.")
        return FillPreview(week, slots)

    def fill(
        self,
        sheet: WeeklySheet,
        preview: FillPreview,
        replace: frozenset[int],
        *,
        confirmed: bool,
    ) -> FillResult:
        if not confirmed:
            raise ValueError("Confirm Fill Testhuset timesheet before changing any hours.")
        if not replace <= set(range(len(preview.slots))):
            raise ValueError("Invalid conflict selection.")
        # Reconcile again before the first write; never apply an obsolete preview.
        self.scan(sheet, preview.week)
        if self.proposed_slots(preview.week) != tuple(s.proposed for s in preview.slots):
            raise ValueError("Local hours or task mappings changed. Prepare a new preview.")
        for item in preview.slots:
            if parse_hours(sheet.read(item.proposed)) != parse_hours(item.existing):
                raise ValueError("Testhuset hours changed. Prepare a new preview.")
        changed = matched = kept = 0
        for index, item in enumerate(preview.slots):
            if item.matches:
                matched += 1
            elif index not in replace:
                kept += 1
            else:
                if parse_hours(sheet.read(item.proposed)) != parse_hours(item.existing):
                    raise ValueError("Testhuset changed during filling. Rescan before retrying.")
                sheet.write_verified(item.proposed)
                changed += 1
        return FillResult(changed, kept, matched)
