"""One owned cancellable worker with main-thread delivery and safe shutdown."""

from collections.abc import Callable
from threading import Event

from PySide6.QtCore import QObject, QThread, Signal, Slot

from qi_flow.application.sync_models import SyncJobCancelledError


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


class OwnedOperationController(QObject):
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

    def resume_after_shutdown(self) -> None:
        if self.busy:
            raise RuntimeError("Finish the owned worker before resuming.")
        self._closing = False

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
        worker.wait()
        worker.deleteLater()
        self.busy_changed.emit(False)
        if self._closing:
            self.ready_for_shutdown.emit()
