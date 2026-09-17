"""Windows startup registration, exercised through a fake registry backend (R24, D034)."""

from __future__ import annotations

import pytest

from qi_flow.infrastructure import startup as startup_module
from qi_flow.infrastructure.startup import (
    START_MINIMIZED_FLAG,
    NullStartupManager,
    WindowsStartupManager,
    create_startup_manager,
)


class FakeKey:
    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    def __enter__(self) -> FakeKey:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None


class FakeWinReg:
    """A minimal in-memory stand-in for the ``winreg`` module's Run-key operations."""

    HKEY_CURRENT_USER = object()
    REG_SZ = 1

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def OpenKey(self, _hive: object, _path: str) -> FakeKey:
        return FakeKey(self.values)

    def CreateKeyEx(self, _hive: object, _path: str) -> FakeKey:
        return FakeKey(self.values)

    def QueryValueEx(self, key: FakeKey, name: str) -> tuple[str, int]:
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], self.REG_SZ

    def SetValueEx(self, key: FakeKey, name: str, _reserved: int, _type: int, value: str) -> None:
        self.values[name] = value

    def DeleteValue(self, key: FakeKey, name: str) -> None:
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]


@pytest.fixture
def fake_registry(monkeypatch: pytest.MonkeyPatch) -> FakeWinReg:
    registry = FakeWinReg()
    monkeypatch.setattr(startup_module, "winreg", registry)
    return registry


def test_disabled_by_default(fake_registry: FakeWinReg) -> None:
    manager = WindowsStartupManager(command="qi-flow.exe --start-minimized")
    assert manager.is_enabled() is False


def test_enabling_writes_the_launch_command(fake_registry: FakeWinReg) -> None:
    manager = WindowsStartupManager(command='"C:\\QI Flow\\qi-flow.exe" --start-minimized')
    manager.set_enabled(True)
    assert manager.is_enabled() is True
    assert fake_registry.values["QI Flow"] == '"C:\\QI Flow\\qi-flow.exe" --start-minimized'


def test_disabling_removes_the_value_and_is_idempotent(fake_registry: FakeWinReg) -> None:
    manager = WindowsStartupManager(command="qi-flow.exe --start-minimized")
    manager.set_enabled(True)
    manager.set_enabled(False)
    assert manager.is_enabled() is False
    manager.set_enabled(False)  # Disabling twice must not raise.
    assert manager.is_enabled() is False


def test_default_command_requests_a_minimized_start() -> None:
    manager = WindowsStartupManager()
    assert manager.command.endswith(START_MINIMIZED_FLAG)


def test_registry_unavailable_raises_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(startup_module, "winreg", None)
    manager = WindowsStartupManager(command="qi-flow.exe --start-minimized")
    with pytest.raises(RuntimeError):
        manager.set_enabled(True)


def test_null_manager_is_always_disabled_and_refuses_to_enable() -> None:
    manager = NullStartupManager()
    assert manager.is_enabled() is False
    with pytest.raises(RuntimeError):
        manager.set_enabled(True)


def test_factory_selects_a_manager_for_the_current_platform() -> None:
    manager = create_startup_manager()
    assert hasattr(manager, "is_enabled")
    assert hasattr(manager, "set_enabled")
