"""DSB allocation configuration and explicit weekly time-entry fills."""

from collections.abc import Callable

from qi_flow.application.ports import Clock, IdentifierGenerator, UnitOfWork
from qi_flow.application.testhuset import (
    FillPreview,
    FillResult,
    TaskCache,
    TesthusetService,
    WeeklySheet,
)


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

    def fill(
        self,
        sheet: WeeklySheet,
        preview: FillPreview,
        replace: frozenset[int],
        *,
        confirmed: bool,
    ) -> FillResult:
        """Fill DSB entries, then explicitly send the reviewed batch to DSB."""
        result = super().fill(sheet, preview, replace, confirmed=confirmed)
        if result.changed:
            commit = getattr(sheet, "commit_verified", None)
            if not callable(commit):
                raise ValueError(
                    "DSB did not provide a confirmed save action. Rescan before retrying."
                )
            commit()
        return result
