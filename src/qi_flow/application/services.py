"""Application service contract to be implemented story by story."""

from __future__ import annotations

from datetime import date
from typing import Protocol

from qi_flow.application.dto import (
    ActiveStateView,
    DaySummaryView,
    FinishDeductionCommand,
    FinishWorkCommand,
    StartDeductionCommand,
    StartWorkCommand,
    UpdateDayDetailsCommand,
)


class TimeTrackingService(Protocol):
    """Use cases consumed by UI controllers.

    Keep this interface focused on user intent. Concrete implementation belongs in this
    package and coordinates domain policies with a UnitOfWork.
    """

    def start_work(self, command: StartWorkCommand) -> ActiveStateView: ...

    def finish_work(self, command: FinishWorkCommand) -> ActiveStateView: ...

    def start_deduction(self, command: StartDeductionCommand) -> ActiveStateView: ...

    def finish_deduction(self, command: FinishDeductionCommand) -> ActiveStateView: ...

    def update_day_details(self, command: UpdateDayDetailsCommand) -> DaySummaryView: ...

    def active_state(self) -> ActiveStateView: ...

    def month(self, year: int, month: int) -> list[DaySummaryView]: ...

    def day(self, work_date: date) -> DaySummaryView: ...
