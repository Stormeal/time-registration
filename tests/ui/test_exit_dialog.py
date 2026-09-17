"""Close app confirmation: never silently end or lose a running session (US10, D031)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pytestqt.qtbot import QtBot

from qi_flow.application.dto import StartDeductionCommand, StartWorkCommand
from qi_flow.application.time_tracking import TimeTrackingApplicationService
from qi_flow.domain.models import DeductionId, DeductionKind, SessionId
from qi_flow.infrastructure.sqlite.database import SQLiteDatabase
from qi_flow.infrastructure.sqlite.repositories import SQLiteUnitOfWork
from qi_flow.ui.exit_dialog import ExitChoice, ExitCoordinator


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


def build_service(
    tmp_path: object, at: datetime
) -> tuple[TimeTrackingApplicationService, FixedClock]:
    database = SQLiteDatabase(tmp_path / "qi-flow.sqlite3")  # type: ignore[operator]
    database.initialize()
    clock = FixedClock(at)
    service = TimeTrackingApplicationService(lambda: SQLiteUnitOfWork(database), clock, FixedIds())
    return service, clock


class RecordingCoordinator(ExitCoordinator):
    """An ExitCoordinator whose modal prompt is replaced with a scripted answer."""

    def __init__(self, service: TimeTrackingApplicationService, answer: ExitChoice) -> None:
        super().__init__(service)
        self._answer = answer
        self.asked_with_lunch_active: bool | None = None

    def ask(self, lunch_active: bool) -> ExitChoice:
        self.asked_with_lunch_active = lunch_active
        return self._answer


def test_exit_without_an_active_session_never_prompts(tmp_path: object, qtbot: QtBot) -> None:
    now = datetime(2026, 9, 15, 9, 0, tzinfo=UTC)
    service, _clock = build_service(tmp_path, now)
    coordinator = RecordingCoordinator(service, ExitChoice.CANCEL)

    with qtbot.waitSignal(coordinator.exit_confirmed, timeout=1000):
        coordinator.request_exit()

    assert coordinator.asked_with_lunch_active is None


def test_cancel_leaves_the_session_running_and_does_not_confirm_exit(
    tmp_path: object, qtbot: QtBot
) -> None:
    now = datetime(2026, 9, 15, 9, 0, tzinfo=UTC)
    service, _clock = build_service(tmp_path, now)
    service.start_work(StartWorkCommand())
    coordinator = RecordingCoordinator(service, ExitChoice.CANCEL)

    confirmed = []
    coordinator.exit_confirmed.connect(lambda: confirmed.append(True))
    coordinator.request_exit()
    qtbot.wait(50)

    assert confirmed == []
    assert service.active_state().session_id is not None


def test_keep_running_and_close_leaves_the_session_active(tmp_path: object, qtbot: QtBot) -> None:
    now = datetime(2026, 9, 15, 9, 0, tzinfo=UTC)
    service, _clock = build_service(tmp_path, now)
    service.start_work(StartWorkCommand())
    coordinator = RecordingCoordinator(service, ExitChoice.KEEP_RUNNING)

    with qtbot.waitSignal(coordinator.exit_confirmed, timeout=1000):
        coordinator.request_exit()

    assert service.active_state().session_id is not None
    assert coordinator.asked_with_lunch_active is False


def test_finish_and_close_finishes_the_session_before_confirming(
    tmp_path: object, qtbot: QtBot
) -> None:
    now = datetime(2026, 9, 15, 9, 0, tzinfo=UTC)
    service, clock = build_service(tmp_path, now)
    service.start_work(StartWorkCommand())
    clock.value += timedelta(hours=8)
    coordinator = RecordingCoordinator(service, ExitChoice.FINISH_AND_CLOSE)

    with qtbot.waitSignal(coordinator.exit_confirmed, timeout=1000):
        coordinator.request_exit()

    assert service.active_state().session_id is None


def test_lunch_active_is_reported_to_the_prompt_and_finish_is_never_attempted(
    tmp_path: object, qtbot: QtBot
) -> None:
    now = datetime(2026, 9, 15, 9, 0, tzinfo=UTC)
    service, _clock = build_service(tmp_path, now)
    service.start_work(StartWorkCommand())
    service.start_deduction(StartDeductionCommand(DeductionKind.LUNCH))
    coordinator = RecordingCoordinator(service, ExitChoice.KEEP_RUNNING)

    with qtbot.waitSignal(coordinator.exit_confirmed, timeout=1000):
        coordinator.request_exit()

    assert coordinator.asked_with_lunch_active is True
    state = service.active_state()
    assert state.session_id is not None
    assert state.active_deduction_kind is DeductionKind.LUNCH


def test_default_dialog_never_offers_finish_while_lunch_is_active(tmp_path: object) -> None:
    now = datetime(2026, 9, 15, 9, 0, tzinfo=UTC)
    service, _clock = build_service(tmp_path, now)
    coordinator = ExitCoordinator(service)

    # Exercises the real button construction without exec()-ing the modal dialog.
    box = coordinator._build_dialog(lunch_active=True)
    labels = {button.text() for button in box.buttons()}
    assert "Finish work and close" not in labels
    assert "Keep running and close" in labels
    assert "Cancel" in labels


def test_default_dialog_offers_finish_while_only_working(tmp_path: object) -> None:
    now = datetime(2026, 9, 15, 9, 0, tzinfo=UTC)
    service, _clock = build_service(tmp_path, now)
    coordinator = ExitCoordinator(service)

    box = coordinator._build_dialog(lunch_active=False)
    labels = {button.text() for button in box.buttons()}
    assert "Finish work and close" in labels
