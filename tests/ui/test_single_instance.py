"""Single-instance guard: a second launch focuses the first instead of racing it (US11, D036)."""

from __future__ import annotations

import uuid

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
