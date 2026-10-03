"""Single-instance guard: a second launch focuses the first instead of racing it (US11, D036)."""

from __future__ import annotations

import errno
import sys
import uuid
from pathlib import Path

import pytest
from PySide6.QtNetwork import QLocalServer
from pytestqt.qtbot import QtBot

from qi_flow.infrastructure.single_instance import SingleInstanceGuard


def _unique_key() -> str:
    return uuid.uuid4().hex


def test_first_guard_becomes_primary(qtbot: QtBot) -> None:
    guard = SingleInstanceGuard(_unique_key())
    try:
        assert guard.try_acquire() is True
        assert guard.is_primary is True
    finally:
        guard.release()


def test_second_guard_defers_and_focuses_the_primary(qtbot: QtBot) -> None:
    key = _unique_key()
    primary = SingleInstanceGuard(key)
    secondary = SingleInstanceGuard(key)
    try:
        assert primary.try_acquire() is True

        with qtbot.waitSignal(primary.focus_requested, timeout=2000):
            acquired = secondary.try_acquire()

        assert acquired is False
        assert secondary.is_primary is False
    finally:
        primary.release()
        secondary.release()


def test_releasing_the_primary_frees_the_key_for_a_later_launch(qtbot: QtBot) -> None:
    key = _unique_key()
    first = SingleInstanceGuard(key)
    try:
        assert first.try_acquire() is True
    finally:
        first.release()

    second = SingleInstanceGuard(key)
    try:
        assert second.try_acquire() is True
        assert second.is_primary is True
    finally:
        second.release()


def test_primary_reacquisition_is_idempotent(qtbot: QtBot) -> None:
    guard = SingleInstanceGuard(_unique_key())
    try:
        assert guard.try_acquire() is True
        assert guard.try_acquire() is True
        guard.release()
        guard.release()
        assert guard.is_primary is False
        assert guard.try_acquire() is True
    finally:
        guard.release()


def test_same_data_directory_is_exclusive_even_with_different_keys(
    qtbot: QtBot, tmp_path: Path
) -> None:
    lock_path = tmp_path / "qi-flow.instance.lock"
    first = SingleInstanceGuard(_unique_key(), lock_path=lock_path)
    second = SingleInstanceGuard(_unique_key(), lock_path=lock_path)
    try:
        assert first.try_acquire() is True
        assert second.try_acquire() is False
        first.release()
        assert second.try_acquire() is True
    finally:
        first.release()
        second.release()


def test_lock_file_permission_error_is_reported(qtbot: QtBot, tmp_path: Path) -> None:
    lock_path = tmp_path / "qi-flow.instance.lock"
    lock_path.mkdir()
    guard = SingleInstanceGuard(_unique_key(), lock_path=lock_path)
    try:
        with pytest.raises(OSError):
            guard.try_acquire()
        assert guard.is_primary is False
    finally:
        guard.release()


def test_listen_failure_reports_error_and_releases_ownership(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = _unique_key()
    lock_path = tmp_path / "qi-flow.instance.lock"
    guard = SingleInstanceGuard(key, lock_path=lock_path)
    with monkeypatch.context() as patch:
        patch.setattr(QLocalServer, "listen", lambda self, name: False)
        with pytest.raises(OSError):
            guard.try_acquire()
    replacement = SingleInstanceGuard(key, lock_path=lock_path)
    try:
        assert guard.is_primary is False
        assert replacement.try_acquire() is True
    finally:
        guard.release()
        replacement.release()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows native locking errors")
def test_native_lock_io_failure_is_reported_and_does_not_leak_ownership(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import msvcrt

    def fail_lock(descriptor: int, mode: int, byte_count: int) -> None:
        raise OSError(errno.EIO, "I/O failure")

    key = _unique_key()
    guard = SingleInstanceGuard(key, lock_path=tmp_path / "qi-flow.instance.lock")
    try:
        with monkeypatch.context() as patch:
            patch.setattr(msvcrt, "locking", fail_lock)
            with pytest.raises(OSError) as raised:
                guard.try_acquire()
            assert raised.value.errno == errno.EIO
            assert guard.is_primary is False
        assert guard.try_acquire() is True
    finally:
        guard.release()
