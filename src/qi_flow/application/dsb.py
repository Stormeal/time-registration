"""DSB allocation configuration and explicit weekly time-entry fills."""

from collections.abc import Callable

from qi_flow.application.ports import Clock, IdentifierGenerator, UnitOfWork
from qi_flow.application.testhuset import (
    FillDecision,
    FillDecisions,
    FillPreview,
    FillResult,
    TaskCache,
    TesthusetService,
    WeeklySheet,
)
from qi_flow.domain.models import IsoWeek
from qi_flow.domain.testhuset import parse_hours


class DsbService(TesthusetService):
    """The DSB portal has the same safe scan/preview/fill contract as Testhuset."""

    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        clock: Clock,
        identifiers: IdentifierGenerator,
        cache: TaskCache,
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

    def is_enabled(self) -> bool:
        with self._uow_factory() as uow:
            return uow.settings.get("dsb_enabled") is True

    def set_enabled(self, enabled: bool) -> None:
        with self._uow_factory() as uow:
            uow.settings.save("dsb_enabled", enabled, self._clock.now())

    def preview(self, sheet: WeeklySheet, week: IsoWeek) -> FillPreview:
        """Read the reviewed DSB week through Overview using the latest explicit allocation scan."""
        return self._read_preview(sheet, week)

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
        if self.proposed_slots(preview.week) != tuple(slot.proposed for slot in preview.slots):
            raise ValueError("Local hours or task mappings changed. Prepare a new preview.")
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
