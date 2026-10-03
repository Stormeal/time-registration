"""Runtime ownership barrier shared by exit, restore and explicit update restart."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from PySide6.QtCore import QObject, QThread, Signal, SignalInstance, Slot

from qi_flow.application.desktop import RestartCommand


class ShutdownParticipant(Protocol):
    ready_for_shutdown: SignalInstance

    @property
    def busy(self) -> bool: ...
    def begin_shutdown(self) -> None: ...
    def wait_for_shutdown(self) -> None: ...
    def resume_after_shutdown(self) -> None: ...


@dataclass
class _ThreadOwner:
    cancel: Callable[[], None]
    parent: QObject | None


class ShutdownGroup(QObject):
    ready = Signal()
    closing = Signal()
    resumed = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._controllers: list[ShutdownParticipant] = []
        self._threads: dict[QThread, _ThreadOwner] = {}
        self._closing = self._initiating = self._ready_sent = False

    def register_controller(self, controller: ShutdownParticipant) -> None:
        if self._closing:
            raise RuntimeError("Cannot add runtime work during shutdown.")
        self._controllers.append(controller)
        controller.ready_for_shutdown.connect(self._check_ready)

    def track_thread(self, worker: QThread, cancel: Callable[[], None]) -> bool:
        if self._closing:
            cancel()
            return False
        self._threads[worker] = _ThreadOwner(cancel, worker.parent())
        worker.setParent(self)
        worker.finished.connect(self._thread_finished)
        return True

    @Slot()
    def begin_shutdown(self) -> None:
        if self._closing:
            return
        self._closing = self._initiating = True
        self.closing.emit()
        for controller in self._controllers:
            controller.begin_shutdown()
        for owner in self._threads.values():
            owner.cancel()
        self._initiating = False
        self._check_ready()

    @Slot()
    def _thread_finished(self) -> None:
        worker = self.sender()
        if isinstance(worker, QThread):
            self._finish_thread(worker)

    def _finish_thread(self, worker: QThread) -> None:
        owner = self._threads.pop(worker, None)
        if owner is not None:
            worker.wait()  # Native cleanup must also finish before ownership is released.
            worker.setParent(owner.parent)
        self._check_ready()

    @Slot()
    def _check_ready(self) -> None:
        if (
            self._closing
            and not self._initiating
            and not self._ready_sent
            and not self._threads
            and not any(controller.busy for controller in self._controllers)
        ):
            self._ready_sent = True
            self.ready.emit()

    def wait_for_shutdown(self) -> None:
        self.begin_shutdown()
        for controller in self._controllers:
            controller.wait_for_shutdown()
        for worker in tuple(self._threads):
            worker.wait()
            self._finish_thread(worker)
        self._check_ready()

    def resume(self) -> None:
        if not self._ready_sent:
            raise RuntimeError("Finish cancelling runtime work before resuming.")
        self._closing = self._ready_sent = False
        for controller in self._controllers:
            controller.resume_after_shutdown()
        self.resumed.emit()


class RuntimeLifecycle(QObject):
    ready_to_quit = Signal(object)
    failed = Signal(str)
    frozen = Signal(bool)

    def __init__(self, shutdown: ShutdownGroup, restore: Callable[[object], None]) -> None:
        super().__init__(shutdown)
        self._shutdown, self._restore = shutdown, restore
        self._backup: object | None = None
        self._command: RestartCommand | None = None
        self._pending = False
        shutdown.ready.connect(self._ready)

    def exit(self, command: RestartCommand | None = None) -> None:
        if self._pending:
            return
        self._pending = True
        self._command = command
        self.frozen.emit(True)
        self._shutdown.begin_shutdown()

    def restore(self, backup: object, command: RestartCommand | None = None) -> None:
        if self._pending:
            return
        self._backup = backup
        self.exit(command)

    @Slot()
    def _ready(self) -> None:
        backup, self._backup = self._backup, None
        if backup is not None:
            try:
                self._restore(backup)
            except Exception:
                self._pending = False
                self._shutdown.resume()
                self.frozen.emit(False)
                self.failed.emit(
                    "Restore failed. Your current database remains available; "
                    "review the backup and retry."
                )
                return
        self.ready_to_quit.emit(self._command)
