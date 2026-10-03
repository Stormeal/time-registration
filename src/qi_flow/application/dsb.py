"""DSB allocation configuration and explicit weekly time-entry fills."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from qi_flow.application.ports import Clock, IdentifierGenerator, UnitOfWork
from qi_flow.application.testhuset import (
    FillDecision,
    FillDecisions,
    FillPreview,
    FillResult,
    HourSlot,
    PreviewSlot,
    TaskCache,
    TesthusetService,
    WeeklySheet,
    completed_segments,
)
from qi_flow.domain.models import IsoWeek
from qi_flow.domain.testhuset import ProjectTask, decimal_hours, parse_hours


@dataclass(frozen=True)
class DsbCoverageEntry:
    work_date: date
    session_id: str
    branch_id: str | None
    branch: ProjectTask | None
    seconds: int
    included: bool
    reason: str


@dataclass(frozen=True)
class DsbCoverage:
    entries: tuple[DsbCoverageEntry, ...]

    @property
    def included_seconds(self) -> int:
        return sum(entry.seconds for entry in self.entries if entry.included)

    @property
    def excluded_seconds(self) -> int:
        return sum(entry.seconds for entry in self.entries if not entry.included)


@dataclass(frozen=True)
class DsbPreview(FillPreview):
    coverage: DsbCoverage
    configuration: tuple[object, ...]


class DsbService(TesthusetService):
    """The DSB portal has the same safe scan/preview/fill contract as Testhuset."""

    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        clock: Clock,
        identifiers: IdentifierGenerator,
        cache: TaskCache,
        *,
        testhuset_cache: TaskCache | None = None,
    ) -> None:
        super().__init__(
            uow_factory,
            clock,
            identifiers,
            cache,
            destination="DSB",
            settings_prefix="dsb",
            assignment_attribute="dsb_allocation_id",
        )
        self._testhuset_cache = testhuset_cache

    def branch_tasks(self) -> tuple[ProjectTask, ...]:
        return self._testhuset_cache.load() if self._testhuset_cache is not None else ()

    def included_branches(self) -> frozenset[str]:
        with self._uow_factory() as uow:
            value = uow.settings.get("dsb_included_testhuset_tasks")
        return (
            frozenset(item for item in value if isinstance(item, str))
            if isinstance(value, list)
            else frozenset()
        )

    def set_included_branches(self, task_ids: set[str]) -> None:
        previous = self.included_branches()
        if not task_ids <= {task.id for task in self.branch_tasks()} | previous:
            raise ValueError("Choose DSB branches from the latest scanned Testhuset tasks.")
        with self._uow_factory() as uow:
            if previous != task_ids:
                value = uow.settings.get("dsb_branch_generation")
                now = self._clock.now()
                uow.settings.save("dsb_included_testhuset_tasks", sorted(task_ids), now)
                uow.settings.save(
                    "dsb_branch_generation", (value if type(value) is int else 0) + 1, now
                )

    def _calculate(
        self, week: IsoWeek
    ) -> tuple[DsbCoverage, tuple[HourSlot, ...], tuple[object, ...]]:
        branches = {task.id: task for task in self.branch_tasks()}
        allocations = {task.id: task for task in self.tasks()}
        entries: list[DsbCoverageEntry] = []
        totals: dict[tuple[date, str], int] = {}
        with self._uow_factory() as uow:
            raw = uow.settings.get("dsb_included_testhuset_tasks")
            included = (
                frozenset(item for item in raw if isinstance(item, str))
                if isinstance(raw, list)
                else frozenset()
            )
            default_branch = uow.settings.get("testhuset_default_task")
            default_allocation = uow.settings.get("dsb_default_task")
            config = (
                uow.settings.get("dsb_branch_generation"),
                tuple(sorted(included)),
                default_branch,
                default_allocation,
                tuple(sorted(branches.items())),
                tuple(sorted(allocations.items())),
            )
            for session, day, seconds in completed_segments(uow, week):
                identifier = session.testhuset_task_id or default_branch
                identifier = identifier if isinstance(identifier, str) else None
                branch = branches.get(identifier) if identifier is not None else None
                is_included = branch is not None and identifier in included
                reason = (
                    "included"
                    if is_included
                    else "unresolved"
                    if branch is None
                    else "not_included"
                )
                entries.append(
                    DsbCoverageEntry(
                        day, str(session.id), identifier, branch, seconds, is_included, reason
                    )
                )
                if is_included:
                    allocation = session.dsb_allocation_id or default_allocation
                    if not isinstance(allocation, str) or allocation not in allocations:
                        raise ValueError(
                            "Select a current default DSB allocation or session override "
                            "in Settings."
                        )
                    key = day, allocation
                    totals[key] = totals.get(key, 0) + seconds
        slots = tuple(
            HourSlot(day, allocations[task], decimal_hours(seconds))
            for (day, task), seconds in sorted(totals.items())
        )
        return DsbCoverage(tuple(entries)), slots, config

    def coverage(self, week: IsoWeek) -> DsbCoverage:
        return self._calculate(week)[0]

    def proposed_slots(self, week: IsoWeek) -> tuple[HourSlot, ...]:
        if not self.included_branches():
            raise ValueError(
                "Include at least one scanned Testhuset branch in DSB hours in Settings first."
            )
        return self._calculate(week)[1]

    def is_enabled(self) -> bool:
        with self._uow_factory() as uow:
            return uow.settings.get("dsb_enabled") is True

    def set_enabled(self, enabled: bool) -> None:
        with self._uow_factory() as uow:
            uow.settings.save("dsb_enabled", enabled, self._clock.now())

    def preview(self, sheet: WeeklySheet, week: IsoWeek) -> FillPreview:
        """Read the reviewed DSB week through Overview using the latest explicit allocation scan."""
        self._latest_preview = None
        if not self.included_branches():
            raise ValueError(
                "Include at least one scanned Testhuset branch in DSB hours in Settings first."
            )
        coverage, proposed, config = self._calculate(week)
        slots = tuple(PreviewSlot(slot, sheet.read(slot)) for slot in proposed)
        for slot in slots:
            parse_hours(slot.existing)
        # An entirely excluded week still has an inspectable review, with Fill disabled.
        preview = DsbPreview(week, slots, coverage, config)
        self._latest_preview = preview
        return preview

    def fill(
        self,
        sheet: WeeklySheet,
        preview: FillPreview,
        decisions: FillDecisions,
        *,
        confirmed: bool,
    ) -> FillResult:
        """Fill DSB entries, then explicitly send the reviewed batch to DSB."""
        choices = self._begin_fill(preview, decisions, confirmed=confirmed)
        coverage, proposed, config = self._calculate(preview.week)
        if not isinstance(preview, DsbPreview) or (
            config != preview.configuration
            or coverage != preview.coverage
            or proposed != tuple(slot.proposed for slot in preview.slots)
        ):
            raise ValueError("Local hours or task mappings changed. Prepare a new preview.")
        if not preview.slots:
            raise ValueError(
                "There are no included completed DSB hours to fill. Review excluded branches."
            )
        for item in preview.slots:
            if parse_hours(sheet.read(item.proposed)) != parse_hours(item.existing):
                raise ValueError("DSB hours changed. Prepare a new preview.")
        changed = matched = kept = 0
        for index, item in enumerate(preview.slots):
            if item.matches:
                matched += 1
            elif choices[index] is FillDecision.KEEP:
                kept += 1
            else:
                if parse_hours(sheet.read(item.proposed)) != parse_hours(item.existing):
                    raise ValueError("DSB changed during filling. Rescan before retrying.")
                sheet.write_verified(item.proposed)
                changed += 1
        result = FillResult(changed, kept, matched)
        if result.changed:
            commit = getattr(sheet, "commit_verified", None)
            if not callable(commit):
                raise ValueError(
                    "DSB did not provide a confirmed save action. Rescan before retrying."
                )
            commit()
        return result
