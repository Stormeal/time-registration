"""Tray icon and its compact popover mirror main-window state (US09, D033-D034)."""

from __future__ import annotations

from datetime import UTC, datetime

from PySide6.QtGui import QIcon
from pytestqt.qtbot import QtBot

from qi_flow.application.dto import StartDeductionCommand, StartWorkCommand
from qi_flow.application.time_tracking import TimeTrackingApplicationService
from qi_flow.domain.models import DeductionId, DeductionKind, SessionId
from qi_flow.infrastructure.sqlite.database import SQLiteDatabase
from qi_flow.infrastructure.sqlite.repositories import SQLiteUnitOfWork
from qi_flow.ui.tray import TrayController
from qi_flow.ui.tray_panel import TrayPanel


class FixedClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


class FixedIds:
    def __init__(self) -> None:
        self.session_count = 0
        self.deduction_count = 0

    def session_id(self) -> SessionId:
        self.session_count += 1
        return SessionId(f"session-{self.session_count}")

    def deduction_id(self) -> DeductionId:
        self.deduction_count += 1
        return DeductionId(f"deduction-{self.deduction_count}")

    def audit_id(self) -> str:
        return f"audit-{self.session_count}-{self.deduction_count}"


class FakeStartupManager:
    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled
        self.set_calls: list[bool] = []

    def is_enabled(self) -> bool:
        return self.enabled

    def set_enabled(self, enabled: bool) -> None:
        self.set_calls.append(enabled)
        self.enabled = enabled


def build_service(tmp_path: object, at: datetime) -> TimeTrackingApplicationService:
    database = SQLiteDatabase(tmp_path / "qi-flow.sqlite3")  # type: ignore[operator]
    database.initialize()
    return TimeTrackingApplicationService(
        lambda: SQLiteUnitOfWork(database), FixedClock(at), FixedIds()
    )


def test_menu_offers_start_work_when_stopped(tmp_path: object, qtbot: QtBot) -> None:
    now = datetime(2026, 9, 15, 9, 0, tzinfo=UTC)
    service = build_service(tmp_path, now)
    tray = TrayController(QIcon(), service, FakeStartupManager())
    qtbot.addWidget(tray._panel)

    tray._refresh_menu()

    assert tray._start_work_action.isEnabled() is True
    assert tray._lunch_action.isEnabled() is False
    assert tray._finish_action.isEnabled() is False
    assert tray._state_action.text() == "Not tracking"


def test_menu_reflects_working_state(tmp_path: object, qtbot: QtBot) -> None:
    now = datetime(2026, 9, 15, 9, 0, tzinfo=UTC)
    service = build_service(tmp_path, now)
    service.start_work(StartWorkCommand())
    tray = TrayController(QIcon(), service, FakeStartupManager())
    qtbot.addWidget(tray._panel)

    tray._refresh_menu()

    assert tray._start_work_action.isEnabled() is False
    assert tray._lunch_action.isEnabled() is True
    assert tray._lunch_action.text() == "Start lunch"
    assert tray._finish_action.isEnabled() is True


def test_menu_reflects_lunch_state_and_blocks_finish(tmp_path: object, qtbot: QtBot) -> None:
    now = datetime(2026, 9, 15, 9, 0, tzinfo=UTC)
    service = build_service(tmp_path, now)
    service.start_work(StartWorkCommand())
    service.start_deduction(StartDeductionCommand(DeductionKind.LUNCH))
    tray = TrayController(QIcon(), service, FakeStartupManager())
    qtbot.addWidget(tray._panel)

    tray._refresh_menu()

    assert tray._start_work_action.isEnabled() is False
    assert tray._lunch_action.isEnabled() is True
    assert tray._lunch_action.text() == "End lunch"
    assert tray._finish_action.isEnabled() is False


def test_start_with_windows_toggle_writes_through_to_the_startup_manager(
    tmp_path: object, qtbot: QtBot
) -> None:
    now = datetime(2026, 9, 15, 9, 0, tzinfo=UTC)
    service = build_service(tmp_path, now)
    startup_manager = FakeStartupManager(enabled=False)
    tray = TrayController(QIcon(), service, startup_manager)
    qtbot.addWidget(tray._panel)

    tray._startup_action.trigger()

    assert startup_manager.set_calls == [True]


def test_menu_action_starts_work_through_the_service(tmp_path: object, qtbot: QtBot) -> None:
    now = datetime(2026, 9, 15, 9, 0, tzinfo=UTC)
    service = build_service(tmp_path, now)
    tray = TrayController(QIcon(), service, FakeStartupManager())
    qtbot.addWidget(tray._panel)

    tray._start_work_action.trigger()

    assert service.active_state().session_id is not None


def test_close_app_action_emits_close_app_requested(tmp_path: object, qtbot: QtBot) -> None:
    now = datetime(2026, 9, 15, 9, 0, tzinfo=UTC)
    service = build_service(tmp_path, now)
    tray = TrayController(QIcon(), service, FakeStartupManager())
    qtbot.addWidget(tray._panel)

    with qtbot.waitSignal(tray.close_app_requested, timeout=1000):
        tray._menu.actions()[-1].trigger()


def test_panel_mirrors_the_same_service_state(tmp_path: object, qtbot: QtBot) -> None:
    now = datetime(2026, 9, 15, 9, 0, tzinfo=UTC)
    service = build_service(tmp_path, now)
    panel = TrayPanel(service, lambda: None)
    qtbot.addWidget(panel)

    panel.refresh()
    assert panel._status.text() == "Not tracking"

    service.start_work(StartWorkCommand())
    panel.refresh()
    assert panel._status.text() == "Working"
    assert panel._finish_work.isEnabled() is True


def test_panel_start_button_uses_the_service(tmp_path: object, qtbot: QtBot) -> None:
    now = datetime(2026, 9, 15, 9, 0, tzinfo=UTC)
    service = build_service(tmp_path, now)
    panel = TrayPanel(service, lambda: None)
    qtbot.addWidget(panel)
    panel.refresh()

    panel._start_work.click()

    assert service.active_state().session_id is not None
    assert panel._status.text() == "Working"


def test_panel_open_timesheet_button_hides_panel_and_calls_callback(
    tmp_path: object, qtbot: QtBot
) -> None:
    now = datetime(2026, 9, 15, 9, 0, tzinfo=UTC)
    service = build_service(tmp_path, now)
    calls: list[bool] = []
    panel = TrayPanel(service, lambda: calls.append(True))
    qtbot.addWidget(panel)
    panel.refresh()

    panel._open_timesheet_button.click()

    assert calls == [True]
