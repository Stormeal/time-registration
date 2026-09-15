"""Windows system-tray lifecycle adapter."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon


class TrayController(QObject):
    """Own the tray icon and expose user intent as Qt signals.

    Feature stories will replace the placeholder state/action entries and add the compact panel.
    """

    open_requested = Signal()
    close_app_requested = Signal()

    def __init__(self, icon: QIcon, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._tray = QSystemTrayIcon(icon, self)
        self._tray.setToolTip("QI Flow")

        menu = QMenu()
        open_action = QAction("Open QI Flow", menu)
        open_action.triggered.connect(self.open_requested.emit)
        menu.addAction(open_action)

        state_action = QAction("Not tracking · architecture shell", menu)
        state_action.setEnabled(False)
        menu.addAction(state_action)
        menu.addSeparator()

        close_action = QAction("Close app", menu)
        close_action.triggered.connect(self.close_app_requested.emit)
        menu.addAction(close_action)

        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_activated)

    def show(self) -> None:
        self._tray.show()

    def hide(self) -> None:
        self._tray.hide()

    @Slot(QSystemTrayIcon.ActivationReason)
    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.open_requested.emit()
