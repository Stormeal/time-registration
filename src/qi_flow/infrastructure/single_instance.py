"""Single-instance guard so a second launch focuses the running window (US11, D036).

Implemented with ``QLocalServer``/``QLocalSocket`` rather than an OS-specific mutex, so the same
code path is exercised in tests on any platform. Only one process may hold the live database
connection; a second launch never opens it.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

_CONNECT_TIMEOUT_MS = 500


class SingleInstanceGuard(QObject):
    """Detect whether another QI Flow process is already running for this key.

    ``try_acquire`` returns ``True`` when this process becomes the primary instance; the caller
    should then build the application normally. It returns ``False`` when another instance is
    already running -- a focus request has been sent to it and the caller should exit
    immediately without touching the database. The primary instance emits ``focus_requested``
    whenever a later launch asks to be brought to the front.
    """

    focus_requested = Signal()

    def __init__(self, key: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._server_name = f"qi-flow-{key}"
        self._server: QLocalServer | None = None

    @property
    def is_primary(self) -> bool:
        return self._server is not None

    def try_acquire(self) -> bool:
        """Become the primary instance, or notify the existing one and report failure."""
        probe = QLocalSocket(self)
        probe.connectToServer(self._server_name)
        if probe.waitForConnected(_CONNECT_TIMEOUT_MS):
            probe.write(b"focus")
            probe.flush()
            probe.waitForBytesWritten(_CONNECT_TIMEOUT_MS)
            probe.disconnectFromServer()
            probe.deleteLater()
            return False
        probe.deleteLater()

        # No primary answered; clear a stale socket file left by a crashed process and listen.
        QLocalServer.removeServer(self._server_name)
        server = QLocalServer(self)
        server.newConnection.connect(self._on_new_connection)
        if not server.listen(self._server_name):
            # Another process won the race between this probe and the listen call below.
            return False
        self._server = server
        return True

    def release(self) -> None:
        """Stop listening so the server name is free for the next launch."""
        if self._server is not None:
            self._server.close()
            self._server = None

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
