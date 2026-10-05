"""Owned Google jobs with one-shot opening and committed-finish requests."""

from math import ceil

from PySide6.QtCore import QObject, Qt, QTimer, Slot

from qi_flow.application.ports import GoogleAuthorization
from qi_flow.application.sync_actions import GoogleSyncActions
from qi_flow.application.sync_schedule import SyncSchedule, SyncScheduleState
from qi_flow.ui.operation_controller import OwnedOperationController


class GoogleSyncController(OwnedOperationController):
    def authorize(self, authorization: GoogleAuthorization) -> bool:
        return self.start(
            "authorize",
            lambda cancelled: authorization.authorize(cancelled=cancelled, timeout_seconds=120.0),
        )


class AutomaticSyncController(QObject):
    """Share the owned worker without periodic database or network polling."""

    def __init__(
        self,
        actions: GoogleSyncActions,
        worker: GoogleSyncController,
        schedule: SyncSchedule,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._actions, self._worker, self._schedule = actions, worker, schedule
        self._running: SyncScheduleState | None = None
        self._closed = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self.poll)
        worker.completed.connect(self._completed)
        worker.failed.connect(self._failed)
        worker.started.connect(self._started)
        worker.busy_changed.connect(self._busy_changed)
        self._arm()

    def _arm(self) -> None:
        self._timer.stop()
        if self._closed or self._worker.busy:
            return
        delay = self._schedule.delay_seconds()
        if delay is not None:
            self._timer.start(min(2_147_483_647, ceil(delay * 1000)))

    @Slot()
    def work_finished(self) -> None:
        self._schedule.work_finished()
        self._arm()

    @Slot(bool)
    def _busy_changed(self, busy: bool) -> None:
        self._arm()

    @Slot()
    def poll(self) -> None:
        if self._closed:
            return
        state = self._actions.schedule_state()
        due = self._schedule.due(state, busy=self._worker.busy)
        if self._running is not None and state.binding != self._running.binding:
            self._worker.cancel()
        if state.binding is None and not self._worker.busy:
            self._schedule.started(state)
            self._timer.stop()
            return
        if due:
            self._running = state
            if not self._worker.start(
                "sync",
                self._actions.synchronize,
                valid=lambda: self._actions.schedule_state().binding == state.binding,
            ):
                self._running = None
        self._arm()

    @Slot(str, object)
    def _completed(self, kind: str, result: object) -> None:
        self._finish(kind, succeeded=True)

    @Slot(str)
    def _started(self, kind: str) -> None:
        if kind == "sync":
            self._running = self._actions.schedule_state()
            if self._schedule.due(self._running, busy=False):
                self._schedule.started(self._running)

    @Slot(str, object)
    def _failed(self, kind: str, error: object) -> None:
        delay = getattr(error, "retry_after", 0.0)
        self._finish(kind, succeeded=False, retry_after=delay)

    def _finish(self, kind: str, *, succeeded: bool, retry_after: float = 0) -> None:
        if kind == "sync" and self._running is not None:
            self._schedule.finished(
                self._running.binding, succeeded=succeeded, retry_after=retry_after
            )
            self._running = None
        self._arm()

    @Slot()
    def begin_shutdown(self) -> None:
        self._closed = True
        self._timer.stop()
        self._schedule.close()
        # A two-minute job/15-second HTTP cleanup cannot fit the five-second close budget.
        # Cancel current work; its immutable outbox will be retried on the next opening.
        self._worker.begin_shutdown()

    @Slot()
    def resume(self) -> None:
        self._closed = False
        self._running = None
        self._schedule.resume()
        self._arm()
