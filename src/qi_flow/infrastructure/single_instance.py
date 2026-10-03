"""Single-instance guard so a second launch focuses the running window (US11, D036).

Ownership is a file lock acquired before the focus socket starts listening. Windows uses a
native byte-range lock, released by the OS after a crash without consulting hostname/PID
metadata. Other platforms use QLockFile. The local socket only delivers focus requests.
"""

from __future__ import annotations

import errno
import hashlib
import os
from pathlib import Path

from PySide6.QtCore import QDir, QLockFile, QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

_CONNECT_TIMEOUT_MS = 500


class SingleInstanceGuard(QObject):
    """Detect whether another QI Flow process is already running for this key.

    ``try_acquire`` returns ``True`` when this process becomes the primary instance; the caller
    should then build the application normally. It returns ``False`` when another instance is
    already running. Focus delivery is best effort; the caller must exit without touching the
    database even if the primary is not listening yet. The primary emits ``focus_requested``
    whenever a later launch asks to be brought to the front.
    """

    focus_requested = Signal()

    def __init__(
        self, key: str, parent: QObject | None = None, *, lock_path: Path | None = None
    ) -> None:
        super().__init__(parent)
        if lock_path is None:
            digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
            lock_path = Path(QDir.tempPath()) / f"qi-flow-{digest}.instance.lock"
        self._lock_path = lock_path.resolve()
        identity = os.path.normcase(str(self._lock_path))
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        self._server_name = f"qi-flow-{digest}"
        self._server: QLocalServer | None = None
        self._lock_fd: int | None = None
        self._qt_lock: QLockFile | None = None

    @property
    def is_primary(self) -> bool:
        return self._lock_fd is not None or self._qt_lock is not None

    def try_acquire(self) -> bool:
        """Become the primary instance, or notify the existing one and report failure."""
        if self.is_primary:
            return True
        if not self._acquire_lock():
            self._request_focus()
            return False

        # Only the exclusive owner can clean up a stale endpoint. Failure to deliver focus
        # never permits a secondary to remove the endpoint or acquire database ownership.
        server = QLocalServer(self)
        try:
            QLocalServer.removeServer(self._server_name)
            server.newConnection.connect(self._on_new_connection)
            if not server.listen(self._server_name):
                raise OSError(
                    f"Could not listen for QI Flow focus requests: {server.errorString()}"
                )
            self._server = server
        except BaseException:
            server.close()
            server.deleteLater()
            self.release()
            raise
        return True

    def _acquire_lock(self) -> bool:
        if os.name == "nt":
            import msvcrt

            # Do not truncate, write, or unlink this file: its stable identity is the lock.
            # _locking supports a range beyond EOF, so even an empty file needs no writes.
            descriptor = os.open(self._lock_path, os.O_RDWR | os.O_CREAT | os.O_BINARY, 0o600)
            try:
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            except OSError as error:
                os.close(descriptor)
                # Only _locking's EACCES is a locking violation; open errors above, and
                # other I/O errors here, must reach the startup error path.
                if error.errno == errno.EACCES:
                    return False
                raise
            self._lock_fd = descriptor
            return True

        lock = QLockFile(str(self._lock_path))
        lock.setStaleLockTime(0)
        if lock.tryLock(0):
            self._qt_lock = lock
            return True
        if lock.error() == QLockFile.LockError.LockFailedError:
            return False
        if lock.error() == QLockFile.LockError.PermissionError:
            raise PermissionError(
                errno.EACCES, "Cannot create QI Flow instance lock", self._lock_path
            )
        raise OSError(errno.EIO, "Cannot acquire QI Flow instance lock", self._lock_path)

    def _request_focus(self) -> None:
        probe = QLocalSocket(self)
        probe.connectToServer(self._server_name)
        if probe.waitForConnected(_CONNECT_TIMEOUT_MS):
            probe.write(b"focus")
            probe.flush()
            probe.waitForBytesWritten(_CONNECT_TIMEOUT_MS)
            probe.disconnectFromServer()
            probe.deleteLater()
            return
        probe.deleteLater()

    def release(self) -> None:
        """Stop focus delivery before releasing exclusive database ownership."""
        if self._server is not None:
            self._server.close()
            self._server.deleteLater()
            self._server = None
        if self._lock_fd is not None:
            import msvcrt

            descriptor, self._lock_fd = self._lock_fd, None
            try:
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            finally:
                os.close(descriptor)
        if self._qt_lock is not None:
            self._qt_lock.unlock()
            self._qt_lock = None

    def _on_new_connection(self) -> None:
        server = self._server
        if server is None:
            return
        while server.hasPendingConnections():
            socket = server.nextPendingConnection()
            if socket is None:
                continue
            # The connection itself is the signal; draining synchronously here (rather than
            # wiring readyRead/disconnected handlers back onto this short-lived socket) keeps
            # its lifetime simple and avoids a callback firing after deleteLater runs.
            socket.waitForReadyRead(_CONNECT_TIMEOUT_MS)
            socket.readAll()
            socket.disconnectFromServer()
            socket.deleteLater()
            self.focus_requested.emit()
