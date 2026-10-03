"""Pure automatic-sync policy; durable eligibility is supplied by application queries."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from qi_flow.application.ports import Clock
from qi_flow.application.sync_models import SyncTarget

type SyncBinding = tuple[SyncTarget, int]


@dataclass(frozen=True)
class SyncScheduleState:
    binding: SyncBinding | None
    eligible_ids: frozenset[str] = frozenset()


class SyncSchedule:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._binding: SyncBinding | None = None
        self._seen: frozenset[str] = frozenset()
        self._next_check: datetime | None = None
        self._retry_at: datetime | None = None
        self._failures = 0
        self._closing = False

    def _observe(self, state: SyncScheduleState) -> None:
        if self._binding != state.binding:
            self._binding = state.binding
            self._seen = frozenset()
            self._next_check = self._retry_at = None
            self._failures = 0

    def due(self, state: SyncScheduleState, *, busy: bool) -> bool:
        self._observe(state)
        if self._closing or busy or state.binding is None:
            return False
        now = self._clock.now()
        if self._retry_at is not None:
            return now >= self._retry_at
        return (
            self._next_check is None
            or now >= self._next_check
            or bool(state.eligible_ids - self._seen)
        )

    def started(self, state: SyncScheduleState) -> None:
        self._observe(state)
        self._seen = state.eligible_ids
        self._next_check = self._clock.now() + timedelta(minutes=5)

    def finished(
        self, binding: SyncBinding | None, *, succeeded: bool, retry_after: float = 0
    ) -> None:
        if self._closing or binding != self._binding:
            return
        now = self._clock.now()
        if succeeded:
            self._failures = 0
            self._retry_at = None
            self._next_check = now + timedelta(minutes=5)
        else:
            self._failures = min(self._failures + 1, 6)
            delay = max(min(15 * 2 ** (self._failures - 1), 300), retry_after)
            self._retry_at = now + timedelta(seconds=delay)

    def close(self) -> None:
        self._closing = True

    def resume(self) -> None:
        self._closing = False
        self._next_check = self._retry_at = None
        self._failures = 0
