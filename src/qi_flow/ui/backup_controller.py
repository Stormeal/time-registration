"""Coalesced daily SQLite maintenance through an injected application port."""

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from qi_flow.application.backup_schedule import BackupSchedule
from qi_flow.application.backups import BackupOperations, BackupStatusView
from qi_flow.ui.operation_controller import OwnedOperationController


class BackupController(QObject):
    status_changed = Signal(object)
    busy_changed = Signal(bool)
    ready_for_shutdown = Signal()

    def __init__(
        self, backups: BackupOperations, schedule: BackupSchedule, parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self._backups, self._schedule = backups, schedule
        self._closed = False
        self._captured = schedule.capture()
        self._worker = OwnedOperationController(self)
        self._worker.completed.connect(self._completed)
        self._worker.failed.connect(self._failed)
        self._worker.busy_changed.connect(self.busy_changed)
        self._worker.ready_for_shutdown.connect(self.ready_for_shutdown)
        self._timer = QTimer(self)
        self._timer.setInterval(60000)
        self._timer.timeout.connect(self.poll)
        self._timer.start()
        QTimer.singleShot(0, self.poll)

    @property
    def busy(self) -> bool:
        return self._worker.busy

    @Slot()
    def poll(self) -> None:
        self.request_backup()

    def request_backup(self, *, force: bool = False) -> bool:
        if self._closed or self.busy or (not force and not self._schedule.due()):
            return False
        captured = self._schedule.capture()
        self._captured = captured

        def operation(cancelled: Callable[[], bool]) -> tuple[bool, BackupStatusView]:
            backup = self._backups.ensure_daily_backup(
                destination=Path(captured[0]), work_date=captured[1], cancelled=cancelled
            )
            status = self._backups.status()
            succeeded = backup is not None or self._backups.has_daily_backup(
                Path(captured[0]), captured[1]
            )
            return succeeded, status

        return self._worker.start("backup", operation)

    @Slot(str, object)
    def _completed(self, kind: str, result: object) -> None:
        if isinstance(result, tuple) and len(result) == 2:
            succeeded, status = result
            self._schedule.record_attempt(*self._captured, succeeded=bool(succeeded))
            if self._captured == self._schedule.capture() and isinstance(status, BackupStatusView):
                self.status_changed.emit(status)

    @Slot(str, object)
    def _failed(self, kind: str, error: object) -> None:
        self._schedule.record_attempt(*self._captured, succeeded=False)
        if not self._closed:
            self.status_changed.emit(self._backups.status())

    @Slot()
    def begin_shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._timer.stop()
        self._worker.begin_shutdown()

    def wait_for_shutdown(self) -> None:
        self.begin_shutdown()
        self._worker.wait_for_shutdown()
