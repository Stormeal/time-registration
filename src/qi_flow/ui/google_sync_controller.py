"""One owned cancellable worker; UI delivery and shutdown stay on the main thread."""

from collections.abc import Callable
from threading import Event

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot

from qi_flow.application.ports import GoogleAuthorization
from qi_flow.application.sync_actions import GoogleSyncActions
from qi_flow.application.sync_models import SyncJobCancelledError
from qi_flow.application.sync_schedule import SyncSchedule, SyncScheduleState


class _OperationWorker(QThread):
    completed = Signal(str, object)
    failed = Signal(str, object)

    def __init__(
        self, kind: str, operation: Callable[[Callable[[], bool]], object], parent: QObject
    ) -> None:
        super().__init__(parent)
        self.kind, self._operation = kind, operation
        self.cancelled = Event()

    def run(self) -> None:
        try:
            result = self._operation(self.cancelled.is_set)
            if self.cancelled.is_set():
                raise SyncJobCancelledError(
                    "Operation cancelled; unverified changes remain pending."
                )
            self.completed.emit(self.kind, result)
        except Exception as error:
            self.failed.emit(self.kind, error)


class GoogleSyncController(QObject):
    completed = Signal(str, object)
    failed = Signal(str, object)
    busy_changed = Signal(bool)
    ready_for_shutdown = Signal()
    started = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._worker: _OperationWorker | None = None
        self._closing = False
        self._valid: Callable[[], bool] = lambda: True

    @property
    def busy(self) -> bool:
        return self._worker is not None

    def start(
        self,
        kind: str,
        operation: Callable[[Callable[[], bool]], object],
        *,
        valid: Callable[[], bool] = lambda: True,
    ) -> bool:
        if self._closing or self.busy:
            return False
        worker = _OperationWorker(kind, operation, self)
        self._worker = worker
        self._valid = valid
        worker.completed.connect(self._completed)
        worker.failed.connect(self._failed)
        worker.finished.connect(self._finished)
        self.busy_changed.emit(True)
        self.started.emit(kind)
        worker.start()
        return True

    def authorize(self, authorization: GoogleAuthorization) -> bool:
        return self.start(
            "authorize",
            lambda cancelled: authorization.authorize(cancelled=cancelled, timeout_seconds=120.0),
        )

    def cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancelled.set()

    def begin_shutdown(self) -> None:
        if self._closing:
            return
        self._closing = True
        self.cancel()
        if not self.busy:
            self.ready_for_shutdown.emit()

    def wait_for_shutdown(self) -> None:
        """Fallback after the event loop exits externally; retain ownership until done."""
        self._closing = True
        self.cancel()
        if self._worker is not None:
            self._worker.wait()
            self._finished()

    @Slot(str, object)
    def _completed(self, kind: str, result: object) -> None:
        if not self._closing and self._valid():
            self.completed.emit(kind, result)

    @Slot(str, object)
    def _failed(self, kind: str, error: object) -> None:
        if not self._closing and self._valid():
            self.failed.emit(kind, error)

    @Slot()
    def _finished(self) -> None:
        worker, self._worker = self._worker, None
        if worker is None:
            return
        worker.deleteLater()
        self.busy_changed.emit(False)
        if self._closing:
            self.ready_for_shutdown.emit()


class AutomaticSyncController(QObject):
    """Poll committed eligibility, sharing the owned worker with manual commands."""

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
        self._timer.setInterval(5000)
        self._timer.timeout.connect(self.poll)
        worker.completed.connect(self._completed)
        worker.failed.connect(self._failed)
        worker.started.connect(self._started)
        self._timer.start()
        QTimer.singleShot(0, self.poll)

    @Slot()
    def poll(self) -> None:
        if self._closed:
            return
        state = self._actions.schedule_state()
        due = self._schedule.due(state, busy=self._worker.busy)
        if self._running is not None and state.binding != self._running.binding:
            self._worker.cancel()
        if due:
            self._running = state
            if self._worker.start(
                "sync",
                self._actions.synchronize,
                valid=lambda: self._actions.schedule_state().binding == state.binding,
            ):
                self._schedule.started(state)
            else:
                self._running = None

    @Slot(str, object)
    def _completed(self, kind: str, result: object) -> None:
        self._finish(kind, succeeded=True)

    @Slot(str)
    def _started(self, kind: str) -> None:
        if kind == "sync":
            self._running = self._actions.schedule_state()
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

    @Slot()
    def begin_shutdown(self) -> None:
        self._closed = True
        self._timer.stop()
        self._schedule.close()
        # A two-minute job/15-second HTTP cleanup cannot fit the five-second close budget.
        # Cancel current work; its immutable outbox will be retried on the next opening.
        self._worker.begin_shutdown()
