"""Pure daily maintenance policy, independent of Qt, SQLite and the filesystem."""

from collections.abc import Callable
from datetime import date, datetime, timedelta

from qi_flow.application.ports import Clock
from qi_flow.domain.time_rules import COPENHAGEN


class BackupSchedule:
    def __init__(self, clock: Clock, destination: Callable[[], str]) -> None:
        self._clock, self._destination = clock, destination
        self._attempts: dict[tuple[str, date], tuple[datetime, bool]] = {}

    def capture(self) -> tuple[str, date]:
        return self._destination(), self._clock.now().astimezone(COPENHAGEN).date()

    def due(self) -> bool:
        attempt = self._attempts.get(self.capture())
        return attempt is None or (
            not attempt[1] and self._clock.now() >= attempt[0] + timedelta(minutes=15)
        )

    def record_attempt(self, destination: str, work_date: date, *, succeeded: bool) -> None:
        # Bound memory when the app stays open for months or folders change repeatedly.
        cutoff = self._clock.now() - timedelta(days=31)
        self._attempts = {key: value for key, value in self._attempts.items() if value[0] >= cutoff}
        self._attempts[(destination, work_date)] = self._clock.now(), succeeded
