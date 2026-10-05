"""One-shot opening/finish requests, with undo grace and server cooldown."""

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
        self._requested_at: datetime | None = clock.now()
        self._retry_at: datetime | None = None
        self._failures = 0
        self._closing = False

    def _observe(self, state: SyncScheduleState) -> None:
        if self._binding != state.binding:
            self._binding = state.binding
            self._retry_at = None
            self._failures = 0

    def due(self, state: SyncScheduleState, *, busy: bool) -> bool:
        self._observe(state)
        if self._closing or busy or state.binding is None or self._requested_at is None:
            return False
        now = self._clock.now()
        return now >= self._requested_at and (self._retry_at is None or now >= self._retry_at)

    def work_finished(self) -> None:
        if not self._closing:
            self._requested_at = self._clock.now() + timedelta(seconds=30)

    def delay_seconds(self) -> float | None:
        if self._closing or self._requested_at is None:
            return None
        when = max(self._requested_at, self._retry_at or self._requested_at)
        return max(0.0, (when - self._clock.now()).total_seconds())

    def started(self, state: SyncScheduleState) -> None:
        self._observe(state)
        self._requested_at = None

    def finished(
        self, binding: SyncBinding | None, *, succeeded: bool, retry_after: float = 0
    ) -> None:
        if self._closing or binding != self._binding:
            return
        now = self._clock.now()
        if succeeded:
            self._failures = 0
            self._retry_at = None
        else:
            self._failures = min(self._failures + 1, 6)
            delay = max(min(15 * 2 ** (self._failures - 1), 300), retry_after)
            try:
                self._retry_at = now + timedelta(seconds=delay)
            except OverflowError:
                # A server delay beyond datetime's range must not escape into a Qt slot.
                # Reconfiguration or an explicit manual sync can still recover the binding.
                self._retry_at = datetime.max.replace(tzinfo=now.tzinfo)

    def close(self) -> None:
        self._closing = True
        self._requested_at = None

    def resume(self) -> None:
        self._closing = False
        self._requested_at = self._clock.now()
        self._retry_at = None
        self._failures = 0
