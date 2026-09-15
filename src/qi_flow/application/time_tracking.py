"""Concrete use cases for timer-based work and lunch tracking."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from qi_flow.application.dto import (
    ActiveStateView,
    FinishDeductionCommand,
    FinishWorkCommand,
    RecoveryView,
    StartDeductionCommand,
    StartWorkCommand,
)
from qi_flow.application.ports import Clock, IdentifierGenerator, UnitOfWork
from qi_flow.domain.errors import InvalidStateTransitionError, RecoveryRequiredError
from qi_flow.domain.models import Deduction, EntrySource, SessionId, WorkSession
from qi_flow.domain.time_rules import (
    VALID_ROUNDING_MINUTES,
    began_on_previous_local_day,
    effective_interval,
    net_seconds,
)


@dataclass(frozen=True, slots=True)
class _UndoAction:
    kind: str
    session_id: str
    occurred_at: datetime


class TimeTrackingApplicationService:
    """Coordinates state transitions and commits each one before returning success."""

    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        clock: Clock,
        identifiers: IdentifierGenerator,
        rounding_minutes: int = 5,
    ) -> None:
        if rounding_minutes not in VALID_ROUNDING_MINUTES:
            raise ValueError("rounding must be one of 1, 5, 10, or 15 minutes")
        self._uow_factory = uow_factory
        self._clock = clock
        self._identifiers = identifiers
        self._rounding_minutes = rounding_minutes
        self._undo_action: _UndoAction | None = None

    @property
    def rounding_minutes(self) -> int:
        with self._uow_factory() as uow:
            saved = uow.settings.get("rounding_minutes")
        return int(saved) if saved in VALID_ROUNDING_MINUTES else self._rounding_minutes

    def set_rounding_minutes(self, minutes: int) -> None:
        if minutes not in VALID_ROUNDING_MINUTES:
            raise ValueError("rounding must be one of 1, 5, 10, or 15 minutes")
        self._rounding_minutes = minutes
        with self._uow_factory() as uow:
            uow.settings.save("rounding_minutes", minutes, self._when(None))

    def start_work(self, command: StartWorkCommand) -> ActiveStateView:
        now = self._when(command.occurred_at)
        rounding_minutes = self.rounding_minutes
        with self._uow_factory() as uow:
            active = uow.sessions.get_active()
            self._ensure_no_blocking_session(active, now)
            session = WorkSession(
                id=self._identifiers.session_id(),
                actual_started_at=now,
                source=EntrySource.TIMER,
                created_at=now,
                updated_at=now,
                rounding_minutes=rounding_minutes,
            )
            uow.sessions.add(session)
        self._undo_action = _UndoAction("start", str(session.id), now)
        return self.active_state(now)

    def finish_work(self, command: FinishWorkCommand) -> ActiveStateView:
        now = self._when(command.occurred_at)
        with self._uow_factory() as uow:
            session = self._require_active(uow)
            if uow.deductions.get_active(session.id) is not None:
                raise InvalidStateTransitionError("End lunch before finishing work.")
            self._complete_session(session, now)
            uow.sessions.save(session)
        self._undo_action = _UndoAction("finish", str(session.id), now)
        return ActiveStateView(None, None, None, None, 0)

    def start_deduction(self, command: StartDeductionCommand) -> ActiveStateView:
        now = self._when(command.occurred_at)
        rounding_minutes = self.rounding_minutes
        with self._uow_factory() as uow:
            session = self._require_active(uow)
            self._ensure_not_blocked(session, now)
            if uow.deductions.get_active(session.id) is not None:
                raise InvalidStateTransitionError("A deduction is already running.")
            deduction = Deduction(
                id=self._identifiers.deduction_id(),
                session_id=session.id,
                kind=command.kind,
                actual_started_at=now,
                source=EntrySource.TIMER,
                created_at=now,
                updated_at=now,
                rounding_minutes=rounding_minutes,
            )
            uow.deductions.add(deduction)
        return self.active_state(now)

    def finish_deduction(self, command: FinishDeductionCommand) -> ActiveStateView:
        now = self._when(command.occurred_at)
        with self._uow_factory() as uow:
            session = self._require_active(uow)
            self._ensure_not_blocked(session, now)
            deduction = uow.deductions.get_active(session.id)
            if deduction is None:
                raise InvalidStateTransitionError("No lunch or break is running.")
            start, end = effective_interval(
                deduction.actual_started_at, now, deduction.rounding_minutes
            )
            deduction.actual_ended_at = now
            deduction.effective_started_at = start
            deduction.effective_ended_at = end
            deduction.updated_at = now
            deduction.revision += 1
            uow.deductions.save(deduction)
        return self.active_state(now)

    def active_state(self, now: datetime | None = None) -> ActiveStateView:
        instant = self._when(now)
        with self._uow_factory() as uow:
            session = uow.sessions.get_active()
            if session is None:
                return ActiveStateView(None, None, None, None, 0)
            deduction = uow.deductions.get_active(session.id)
            return ActiveStateView(
                session.id,
                session.actual_started_at,
                deduction.kind if deduction else None,
                deduction.actual_started_at if deduction else None,
                net_seconds(session, uow.deductions.list_for_session(session.id), instant),
            )

    def recovery_state(self, now: datetime | None = None) -> RecoveryView | None:
        instant = self._when(now)
        with self._uow_factory() as uow:
            session = uow.sessions.get_active()
            if session is None or session.recovery_acknowledged_at is not None:
                return None
            if not began_on_previous_local_day(session, instant):
                return None
            return RecoveryView(
                session.id,
                session.actual_started_at,
                uow.deductions.get_active(session.id) is not None,
            )

    def can_undo_timer_action(self) -> bool:
        action = self._undo_action
        return action is not None and self._when(None) - action.occurred_at <= timedelta(seconds=30)

    def undo_last_timer_action(self) -> ActiveStateView:
        action = self._undo_action
        now = self._when(None)
        if action is None or now - action.occurred_at > timedelta(seconds=30):
            self._undo_action = None
            raise InvalidStateTransitionError("The 30-second undo period has expired.")
        with self._uow_factory() as uow:
            session = uow.sessions.get(SessionId(action.session_id))
            if session is None:
                raise InvalidStateTransitionError("The timer action can no longer be undone.")
            if action.kind == "start":
                if not session.is_active or uow.deductions.get_active(session.id) is not None:
                    raise InvalidStateTransitionError("The timer action can no longer be undone.")
                session.deleted_at = now
            else:
                if uow.sessions.get_active() is not None:
                    raise InvalidStateTransitionError("The timer action can no longer be undone.")
                session.actual_ended_at = None
                session.effective_started_at = None
                session.effective_ended_at = None
            session.updated_at = now
            session.revision += 1
            uow.sessions.save(session)
        self._undo_action = None
        return self.active_state(now)

    def continue_recovery(self) -> ActiveStateView:
        now = self._when(None)
        with self._uow_factory() as uow:
            session = self._require_active(uow)
            session.recovery_acknowledged_at = now
            session.updated_at = now
            session.revision += 1
            uow.sessions.save(session)
        return self.active_state(now)

    def delete_recovery(self) -> ActiveStateView:
        now = self._when(None)
        with self._uow_factory() as uow:
            session = self._require_active(uow)
            session.deleted_at = now
            session.updated_at = now
            session.revision += 1
            uow.sessions.save(session)
        return ActiveStateView(None, None, None, None, 0)

    def _complete_session(self, session: WorkSession, now: datetime) -> None:
        start, end = effective_interval(session.actual_started_at, now, session.rounding_minutes)
        session.actual_ended_at = now
        session.effective_started_at = start
        session.effective_ended_at = end
        session.updated_at = now
        session.revision += 1

    @staticmethod
    def _require_active(uow: UnitOfWork) -> WorkSession:
        session = uow.sessions.get_active()
        if session is None:
            raise InvalidStateTransitionError("Start work before using this action.")
        return session

    def _ensure_no_blocking_session(self, active: WorkSession | None, now: datetime) -> None:
        if active is None:
            return
        self._ensure_not_blocked(active, now)
        raise InvalidStateTransitionError("Work is already running.")

    @staticmethod
    def _ensure_not_blocked(session: WorkSession, now: datetime) -> None:
        if began_on_previous_local_day(session, now) and session.recovery_acknowledged_at is None:
            raise RecoveryRequiredError("Resolve the unfinished previous-day session first.")

    def _when(self, supplied: datetime | None) -> datetime:
        value = supplied or self._clock.now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must be timezone-aware")
        return value.astimezone(UTC)
