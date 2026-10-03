"""UI lifecycle tests for the stable desktop shell."""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox
from pytestqt.qtbot import QtBot

from qi_flow import __version__
from qi_flow.application.dto import StartWorkCommand
from qi_flow.domain.models import WorkSession
from qi_flow.infrastructure.sqlite.repositories import SQLiteWorkSessionRepository
from qi_flow.ui.exit_dialog import ExitChoice, ExitCoordinator
from qi_flow.ui.main_window import MainWindow


def test_closing_main_window_hides_it_for_tray_lifecycle(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    window.close()

    assert not window.isVisible()


def test_show_timesheet_reveals_the_window_on_the_timesheet_page(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.hide()

    window.show_timesheet()

    assert window.isVisible()
    assert window._navigation.currentIndex() == window._page_index["Timesheet"]
    assert window._pages.currentIndex() == window._page_index["Timesheet"]


def test_show_settings_reveals_the_window_on_the_settings_page(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.hide()

    window.show_settings()

    assert window.isVisible()
    assert window._navigation.currentIndex() == window._page_index["Settings"]
    assert window._pages.currentIndex() == window._page_index["Settings"]


def test_navigation_shows_the_application_version_at_its_bottom(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)

    assert window._version_label.text() == f"Version {__version__}"
    assert window._version_label.objectName() == "applicationVersion"


def test_no_tray_close_requests_exit_and_retains_access_until_confirmed(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.set_tray_available(False)
    window.show()
    requested: list[bool] = []
    window.close_app_requested.connect(lambda: requested.append(True))

    window.close()

    assert requested == [True]
    assert window.isVisible()
    assert window._close_app_button.isVisible()
    qtbot.mouseClick(window._close_app_button, Qt.MouseButton.LeftButton)
    assert requested == [True, True]


def test_tray_lifecycle_hides_fallback_exit_action(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.set_tray_available(False)
    window.set_tray_available(True)
    window.show()
    assert not window._close_app_button.isVisible()


@pytest.mark.parametrize(
    ("choice", "confirmed", "active"),
    [
        (ExitChoice.CANCEL, False, True),
        (ExitChoice.KEEP_RUNNING, True, True),
        (ExitChoice.FINISH_AND_CLOSE, True, False),
    ],
)
def test_no_tray_window_close_uses_active_session_exit_choices(
    qtbot: QtBot,
    rig: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    choice: ExitChoice,
    confirmed: bool,
    active: bool,
) -> None:
    rig.service.start_work(StartWorkCommand())
    rig.clock.value += timedelta(hours=1)
    window = MainWindow(*rig.window_args)
    qtbot.addWidget(window)
    window.set_tray_available(False)
    coordinator = ExitCoordinator(rig.service, window)
    monkeypatch.setattr(coordinator, "ask", lambda _lunch: choice)
    exits: list[bool] = []
    coordinator.exit_confirmed.connect(lambda: exits.append(True))
    window.close_app_requested.connect(coordinator.request_exit)
    window.show()

    window.close()

    assert bool(exits) is confirmed
    assert (rig.service.active_state().session_id is not None) is active
    assert window.isVisible()


@pytest.mark.parametrize("use_button", [False, True])
def test_no_tray_inactive_exit_confirms_without_prompt(
    qtbot: QtBot,
    rig: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    use_button: bool,
) -> None:
    window = MainWindow(*rig.window_args)
    qtbot.addWidget(window)
    window.set_tray_available(False)
    coordinator = ExitCoordinator(rig.service, window)
    monkeypatch.setattr(coordinator, "ask", lambda _lunch: pytest.fail("No active session to end"))
    exits: list[bool] = []
    coordinator.exit_confirmed.connect(lambda: exits.append(True))
    coordinator.exit_confirmed.connect(window.hide)
    window.close_app_requested.connect(coordinator.request_exit)
    window.show()

    if use_button:
        qtbot.mouseClick(window._close_app_button, Qt.MouseButton.LeftButton)
    else:
        window.close()

    assert exits == [True]
    assert not window.isVisible()
    assert rig.service.active_state().session_id is None


@pytest.mark.parametrize("use_button", [False, True])
def test_no_tray_failed_finish_retains_window_and_persisted_timer_until_retry(
    qtbot: QtBot,
    rig: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    use_button: bool,
) -> None:
    session_id = rig.service.start_work(StartWorkCommand()).session_id
    rig.clock.value += timedelta(hours=1)
    window = MainWindow(*rig.window_args)
    qtbot.addWidget(window)
    window.set_tray_available(False)
    coordinator = ExitCoordinator(rig.service, window)
    monkeypatch.setattr(coordinator, "ask", lambda _lunch: ExitChoice.FINISH_AND_CLOSE)
    exits: list[bool] = []
    warnings: list[str] = []
    coordinator.exit_confirmed.connect(lambda: exits.append(True))
    coordinator.exit_confirmed.connect(window.hide)
    window.close_app_requested.connect(coordinator.request_exit)
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, _title, text: warnings.append(text))
    window.show()
    real_save = SQLiteWorkSessionRepository.save

    def fail_after_write(repository: SQLiteWorkSessionRepository, session: WorkSession) -> None:
        real_save(repository, session)
        raise sqlite3.OperationalError("Synthetic write failure")

    with monkeypatch.context() as patch:
        patch.setattr(SQLiteWorkSessionRepository, "save", fail_after_write)
        if use_button:
            qtbot.mouseClick(window._close_app_button, Qt.MouseButton.LeftButton)
        else:
            window.close()

    assert exits == []
    assert warnings
    assert window.isVisible()
    assert window._close_app_button.isVisible()
    assert rig.service.active_state().session_id == session_id
    with rig.uow() as uow:
        persisted = uow.sessions.get_active()
        assert persisted is not None
        assert persisted.id == session_id
        assert persisted.actual_ended_at is None

    qtbot.mouseClick(window._close_app_button, Qt.MouseButton.LeftButton)

    assert exits == [True]
    assert not window.isVisible()
    assert rig.service.active_state().session_id is None
