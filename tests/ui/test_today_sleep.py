"""Sleep-gap recovery prompt behavior (US08)."""

from datetime import UTC, datetime, timedelta

from qi_flow.application.dto import StartWorkCommand
from qi_flow.application.time_tracking import TimeTrackingApplicationService
from qi_flow.infrastructure.sqlite.database import SQLiteDatabase
from qi_flow.infrastructure.sqlite.repositories import SQLiteUnitOfWork
from qi_flow.infrastructure.system import UuidIdentifierGenerator
from qi_flow.ui.today_page import TodayPage


class Clock:
    def now(self) -> datetime:
        return datetime.now(UTC)


def build_service(tmp_path):
    database = SQLiteDatabase(tmp_path / "today-sleep.sqlite3")
    database.initialize()
    service = TimeTrackingApplicationService(
        lambda: SQLiteUnitOfWork(database), Clock(), UuidIdentifierGenerator()
    )
    now = Clock().now()
    service.start_work(StartWorkCommand(now - timedelta(hours=2)))
    service.detect_sleep_gap(now - timedelta(hours=1), now)
    assert service.pending_sleep_gap() is not None
    return service


def test_decide_later_keeps_gap_pending_and_timer_actions_disabled(
    qtbot, tmp_path, monkeypatch
) -> None:
    service = build_service(tmp_path)
    messages = []

    class MessageBox:
        class ButtonRole:
            AcceptRole = 1
            DestructiveRole = 2
            RejectRole = 3

        def __init__(self, _parent):
            self.buttons = []
            self.default_button = None
            messages.append(self)

        def setWindowTitle(self, _title):
            pass

        def setText(self, _text):
            pass

        def addButton(self, label, _role):
            self.buttons.append(label)
            return label

        def setDefaultButton(self, button):
            self.default_button = button

        def exec(self):
            return None

        def clickedButton(self):
            return None

    monkeypatch.setattr("qi_flow.ui.today_page.QMessageBox", MessageBox)
    page = TodayPage(service)
    qtbot.addWidget(page)

    assert page._sleep_deferred
    assert messages[0].default_button == "Decide later"
    assert page._start_work.isEnabled() is False
    assert page._finish_work.isEnabled() is False
    assert service.pending_sleep_gap() is not None

    service.resolve_sleep_gap("include")
    page.refresh()
    assert page._finish_work.isEnabled()
    page._refresh_timer.stop()
