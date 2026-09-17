"""Optional per-user "Start with Windows" registration (R24, D034-D035).

The registry is the single source of truth for whether QI Flow starts with Windows; no
duplicate flag is kept in application settings. Registration launches QI Flow with
``--start-minimized`` so automatic startup stays in the tray (D035) unless recovery is needed.
"""

from __future__ import annotations

import sys
from contextlib import suppress
from types import ModuleType
from typing import Protocol

try:  # pragma: no cover - exercised only on Windows
    import winreg
except ImportError:  # pragma: no cover - exercised only off Windows
    winreg = None  # type: ignore[assignment]

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "QI Flow"
START_MINIMIZED_FLAG = "--start-minimized"


class StartupManager(Protocol):
    """Reads and writes optional automatic-startup registration."""

    def is_enabled(self) -> bool: ...

    def set_enabled(self, enabled: bool) -> None: ...


class WindowsStartupManager:
    """Registers/removes a per-user ``HKCU`` Run entry via the Windows registry."""

    def __init__(self, command: str | None = None) -> None:
        self._command = command or self._default_command()

    @property
    def command(self) -> str:
        """The command that would be written to the registry when enabled."""
        return self._command

    def is_enabled(self) -> bool:
        registry = self._require_registry()
        try:
            with registry.OpenKey(registry.HKEY_CURRENT_USER, _RUN_KEY) as key:
                registry.QueryValueEx(key, _VALUE_NAME)
                return True
        except FileNotFoundError:
            return False

    def set_enabled(self, enabled: bool) -> None:
        registry = self._require_registry()
        with registry.CreateKeyEx(registry.HKEY_CURRENT_USER, _RUN_KEY) as key:
            if enabled:
                registry.SetValueEx(key, _VALUE_NAME, 0, registry.REG_SZ, self._command)
            else:
                with suppress(FileNotFoundError):
                    registry.DeleteValue(key, _VALUE_NAME)

    def _require_registry(self) -> ModuleType:
        if winreg is None:
            raise RuntimeError("Windows startup registration is unavailable on this platform.")
        return winreg

    @staticmethod
    def _default_command() -> str:
        if getattr(sys, "frozen", False):
            return f'"{sys.executable}" {START_MINIMIZED_FLAG}'
        return f'"{sys.executable}" -m qi_flow {START_MINIMIZED_FLAG}'


class NullStartupManager:
    """No-op fallback used when startup registration is unsupported on this platform."""

    def is_enabled(self) -> bool:
        return False

    def set_enabled(self, enabled: bool) -> None:
        raise RuntimeError("Start with Windows is unavailable on this platform.")


def create_startup_manager() -> StartupManager:
    """Select the concrete startup manager for the running platform."""
    if sys.platform == "win32":
        return WindowsStartupManager()
    return NullStartupManager()
