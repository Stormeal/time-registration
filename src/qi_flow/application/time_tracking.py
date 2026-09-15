"""Concrete use cases for timer-based work and lunch tracking."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import cast

from qi_flow.application.dto import (
    ActiveStateView,
    DaySummaryView,
    FinishDeductionCommand,
    FinishWorkCommand,
    ManualDeductionCommand,
    ManualWorkSessionCommand,
    RecoveryView,
    SleepGapView,
    StartDeductionCommand,
    StartWorkCommand,
    UpdateDayDetailsCommand,
    UpdateDeductionCommand,
    UpdateWorkSessionCommand,
)
from qi_flow.application.ports import Clock, IdentifierGenerator, UnitOfWork
from qi_flow.domain.errors import (
    InvalidIntervalError,
    InvalidStateTransitionError,
    OverlappingIntervalError,
    RecoveryRequiredError,
)
from qi_flow.domain.models import (
    DayDetails,
    Deduction,
    DeductionId,
    DeductionKind,
    EntrySource,
    SessionId,
    WorkSession,
)
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

    def add_manual_session(self, command: ManualWorkSessionCommand) -> WorkSession:
        start, end = self._completed_times(command.started_at, command.ended_at)
        now = self._when(None)
        with self._uow_factory() as uow:
            self._ensure_session_has_no_overlap(uow, start, end)
            session = WorkSession(
                id=self._identifiers.session_id(),
                actual_started_at=start,
                actual_ended_at=end,
                effective_started_at=start,
                effective_ended_at=end,
                source=EntrySource.MANUAL,
                created_at=now,
                updated_at=now,
                rounding_minutes=1,
            )
            uow.sessions.add(session)
        return session

    def update_work_session(self, command: UpdateWorkSessionCommand) -> WorkSession:
        start, end = self._completed_times(command.started_at, command.ended_at)
        now = self._when(None)
        with self._uow_factory() as uow:
            session = uow.sessions.get(command.session_id)
            if session is None or session.deleted_at is not None or session.is_active:
                raise InvalidStateTransitionError("Choose a completed work session to edit.")
            self._ensure_session_has_no_overlap(uow, start, end, session.id)
            deductions = uow.deductions.list_for_session(session.id)
            if any(
                deduction.deleted_at is None
                and (
                    deduction.actual_started_at < start
                    or deduction.actual_ended_at is None
                    or deduction.actual_ended_at > end
                )
                for deduction in deductions
            ):
                raise InvalidIntervalError(
                    "The work session must still contain all of its lunches and breaks."
                )
            self._record_session_audit(uow, session, "update", now)
            session.actual_started_at = start
            session.actual_ended_at = end
            session.effective_started_at = start
            session.effective_ended_at = end
            session.source = EntrySource.MANUAL
            session.updated_at = now
            session.revision += 1
            uow.sessions.save(session)
        return session

    def add_manual_deduction(self, command: ManualDeductionCommand) -> Deduction:
        start, end = self._completed_times(command.started_at, command.ended_at)
        now = self._when(None)
        with self._uow_factory() as uow:
            session = uow.sessions.get(command.session_id)
            if session is None or session.deleted_at is not None or session.actual_ended_at is None:
                raise InvalidStateTransitionError(
                    "A manual lunch or break needs a completed work session."
                )
            self._ensure_deduction_fits(uow, session, start, end)
            deduction = Deduction(
                id=self._identifiers.deduction_id(),
                session_id=session.id,
                kind=command.kind,
                actual_started_at=start,
                actual_ended_at=end,
                effective_started_at=start,
                effective_ended_at=end,
                source=EntrySource.MANUAL,
                created_at=now,
                updated_at=now,
                rounding_minutes=1,
            )
            uow.deductions.add(deduction)
        return deduction

    def update_deduction(self, command: UpdateDeductionCommand) -> Deduction:
        start, end = self._completed_times(command.started_at, command.ended_at)
        now = self._when(None)
        with self._uow_factory() as uow:
            deduction = uow.deductions.get(command.deduction_id)
            if deduction is None or deduction.deleted_at is not None or deduction.is_active:
                raise InvalidStateTransitionError("Choose a completed lunch or break to edit.")
            session = uow.sessions.get(deduction.session_id)
            if session is None or session.actual_ended_at is None:
                raise InvalidStateTransitionError("The parent work session is unavailable.")
            self._ensure_deduction_fits(uow, session, start, end, deduction.id)
            self._record_deduction_audit(uow, deduction, "update", now)
            deduction.actual_started_at = start
            deduction.actual_ended_at = end
            deduction.effective_started_at = start
            deduction.effective_ended_at = end
            deduction.source = EntrySource.MANUAL
            deduction.updated_at = now
            deduction.revision += 1
            uow.deductions.save(deduction)
        return deduction

    def delete_work_session(self, session_id: SessionId) -> None:
        now = self._when(None)
        with self._uow_factory() as uow:
            session = uow.sessions.get(session_id)
            if session is None or session.deleted_at is not None:
                raise InvalidStateTransitionError("That work session no longer exists.")
            self._record_session_audit(uow, session, "delete", now)
            session.deleted_at = now
            session.updated_at = now
            session.revision += 1
            uow.sessions.save(session)
            for deduction in uow.deductions.list_for_session(session.id):
                if deduction.deleted_at is None:
                    self._record_deduction_audit(uow, deduction, "delete", now)
                    deduction.deleted_at = now
                    deduction.updated_at = now
                    deduction.revision += 1
                    uow.deductions.save(deduction)

    def delete_deduction(self, deduction_id: DeductionId) -> None:
        now = self._when(None)
        with self._uow_factory() as uow:
            deduction = uow.deductions.get(deduction_id)
            if deduction is None or deduction.deleted_at is not None:
                raise InvalidStateTransitionError("That lunch or break no longer exists.")
            self._record_deduction_audit(uow, deduction, "delete", now)
            deduction.deleted_at = now
            deduction.updated_at = now
            deduction.revision += 1
            uow.deductions.save(deduction)

    def restore_work_session(self, session_id: SessionId) -> WorkSession:
        now = self._when(None)
        with self._uow_factory() as uow:
            session = uow.sessions.get(session_id)
            snapshot = uow.audit.latest("work_session", str(session_id))
            if session is None or snapshot is None:
                raise InvalidStateTransitionError(
                    "No recoverable work-session version is available."
                )
            self._restore_session_snapshot(session, snapshot, now)
            if session.is_active and uow.sessions.get_active() not in (None, session):
                raise InvalidStateTransitionError("Cannot restore a second active work session.")
            uow.sessions.save(session)
        return session

    def restore_deduction(self, deduction_id: DeductionId) -> Deduction:
        now = self._when(None)
        with self._uow_factory() as uow:
            deduction = uow.deductions.get(deduction_id)
            snapshot = uow.audit.latest("deduction", str(deduction_id))
            if deduction is None or snapshot is None:
                raise InvalidStateTransitionError(
                    "No recoverable lunch or break version is available."
                )
            self._restore_deduction_snapshot(deduction, snapshot, now)
            uow.deductions.save(deduction)
        return deduction

    def update_day_details(self, command: UpdateDayDetailsCommand) -> DaySummaryView:
        with self._uow_factory() as uow:
            existing = uow.days.get(command.work_date)
            details = DayDetails(
                command.work_date,
                command.location,
                command.note,
                (existing.revision + 1 if existing else 1),
            )
            uow.days.save(details)
        return DaySummaryView(
            command.work_date, None, None, 0, 0, 0, 0, details.location, bool(details.note)
        )

    def day_details(self, work_date: date) -> DayDetails | None:
        with self._uow_factory() as uow:
            return uow.days.get(work_date)

    def completed_sessions(self) -> list[WorkSession]:
        with self._uow_factory() as uow:
            sessions = uow.sessions.list_intersecting(
                datetime(1970, 1, 1, tzinfo=UTC), self._when(None)
            )
        return [session for session in sessions if session.actual_ended_at is not None]

    def sleep_threshold_seconds(self) -> int:
        with self._uow_factory() as uow:
            enabled = uow.settings.get("sleep_detection_enabled")
            minutes = uow.settings.get("sleep_threshold_minutes")
        if enabled is False:
            return 0
        return int(minutes) * 60 if isinstance(minutes, int) and minutes > 0 else 30 * 60

    def sleep_detection_enabled(self) -> bool:
        with self._uow_factory() as uow:
            return uow.settings.get("sleep_detection_enabled") is not False

    def set_sleep_detection(self, enabled: bool, threshold_minutes: int) -> None:
        if threshold_minutes < 1:
            raise ValueError("Sleep threshold must be at least one minute.")
        now = self._when(None)
        with self._uow_factory() as uow:
            uow.settings.save("sleep_detection_enabled", enabled, now)
            uow.settings.save("sleep_threshold_minutes", threshold_minutes, now)

    def detect_sleep_gap(self, started_at: datetime, ended_at: datetime) -> SleepGapView | None:
        start, end = self._when(started_at), self._when(ended_at)
        threshold = self.sleep_threshold_seconds()
        if threshold == 0 or end - start < timedelta(seconds=threshold):
            return None
        with self._uow_factory() as uow:
            session = uow.sessions.get_active()
            if session is None or uow.settings.get("pending_sleep_gap") is not None:
                return None
            if start < session.actual_started_at:
                start = session.actual_started_at
            if end <= start:
                return None
            uow.settings.save(
                "pending_sleep_gap",
                {
                    "session_id": str(session.id),
                    "started_at": start.isoformat(),
                    "ended_at": end.isoformat(),
                },
                end,
            )
            return SleepGapView(session.id, start, end)

    def pending_sleep_gap(self) -> SleepGapView | None:
        with self._uow_factory() as uow:
            value = uow.settings.get("pending_sleep_gap")
        if not isinstance(value, dict):
            return None
        try:
            return SleepGapView(
                SessionId(str(value["session_id"])),
                datetime.fromisoformat(str(value["started_at"])),
                datetime.fromisoformat(str(value["ended_at"])),
            )
        except (KeyError, ValueError):
            return None

    def resolve_sleep_gap(self, resolution: str) -> ActiveStateView:
        now = self._when(None)
        with self._uow_factory() as uow:
            value = uow.settings.get("pending_sleep_gap")
            if not isinstance(value, dict):
                raise InvalidStateTransitionError("There is no sleep interval to resolve.")
            if resolution not in {"include", "exclude"}:
                raise InvalidStateTransitionError(
                    "Choose whether to include or exclude the sleep interval."
                )
            if resolution == "exclude":
                session = uow.sessions.get(SessionId(str(value["session_id"])))
                if session is None or session.deleted_at is not None:
                    raise InvalidStateTransitionError(
                        "The work session for this sleep interval is unavailable."
                    )
                start = datetime.fromisoformat(str(value["started_at"]))
                end = datetime.fromisoformat(str(value["ended_at"]))
                deduction = Deduction(
                    id=self._identifiers.deduction_id(),
                    session_id=session.id,
                    kind=DeductionKind.SLEEP_BREAK,
                    actual_started_at=start,
                    actual_ended_at=end,
                    effective_started_at=start,
                    effective_ended_at=end,
                    source=EntrySource.RECOVERY,
                    created_at=now,
                    updated_at=now,
                    rounding_minutes=1,
                )
                uow.deductions.add(deduction)
            uow.settings.save("pending_sleep_gap", None, now)
        return self.active_state(now)

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
        self._ensure_no_pending_sleep_gap()
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
        self._ensure_no_pending_sleep_gap()
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
        self._ensure_no_pending_sleep_gap()
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

    def _completed_times(
        self, started_at: datetime, ended_at: datetime
    ) -> tuple[datetime, datetime]:
        start, end = self._when(started_at), self._when(ended_at)
        if end <= start:
            raise InvalidIntervalError("End time must be after start time.")
        if end > self._when(None):
            raise InvalidIntervalError("Future time entries are not allowed.")
        return start, end

    @staticmethod
    def _ensure_session_has_no_overlap(
        uow: UnitOfWork, start: datetime, end: datetime, excluded_id: SessionId | None = None
    ) -> None:
        if any(session.id != excluded_id for session in uow.sessions.list_intersecting(start, end)):
            raise OverlappingIntervalError("Work sessions cannot overlap.")

    @staticmethod
    def _ensure_deduction_fits(
        uow: UnitOfWork,
        session: WorkSession,
        start: datetime,
        end: datetime,
        excluded_id: DeductionId | None = None,
    ) -> None:
        if (
            session.actual_ended_at is None
            or start < session.actual_started_at
            or end > session.actual_ended_at
        ):
            raise InvalidIntervalError("Lunches and breaks must stay inside their work session.")
        for deduction in uow.deductions.list_for_session(session.id):
            if deduction.id == excluded_id or deduction.deleted_at is not None:
                continue
            deduction_end = deduction.actual_ended_at
            if (
                deduction_end is not None
                and start < deduction_end
                and end > deduction.actual_started_at
            ):
                raise OverlappingIntervalError("Lunches and breaks cannot overlap.")

    def _record_session_audit(
        self, uow: UnitOfWork, session: WorkSession, action: str, now: datetime
    ) -> None:
        uow.audit.record(
            self._identifiers.audit_id(),
            "work_session",
            str(session.id),
            action,
            self._session_snapshot(session),
            now,
        )

    def _record_deduction_audit(
        self, uow: UnitOfWork, deduction: Deduction, action: str, now: datetime
    ) -> None:
        uow.audit.record(
            self._identifiers.audit_id(),
            "deduction",
            str(deduction.id),
            action,
            self._deduction_snapshot(deduction),
            now,
        )

    @staticmethod
    def _restore_session_snapshot(
        session: WorkSession, snapshot: dict[str, object], now: datetime
    ) -> None:
        session.actual_started_at = datetime.fromisoformat(str(snapshot["actual_started_at"]))
        session.actual_ended_at = TimeTrackingApplicationService._optional_time(
            snapshot, "actual_ended_at"
        )
        session.effective_started_at = TimeTrackingApplicationService._optional_time(
            snapshot, "effective_started_at"
        )
        session.effective_ended_at = TimeTrackingApplicationService._optional_time(
            snapshot, "effective_ended_at"
        )
        session.source = EntrySource(str(snapshot["source"]))
        session.deleted_at = TimeTrackingApplicationService._optional_time(snapshot, "deleted_at")
        session.rounding_minutes = int(cast(int, snapshot["rounding_minutes"]))
        session.updated_at = now
        session.revision += 1

    @staticmethod
    def _restore_deduction_snapshot(
        deduction: Deduction, snapshot: dict[str, object], now: datetime
    ) -> None:
        deduction.actual_started_at = datetime.fromisoformat(str(snapshot["actual_started_at"]))
        deduction.actual_ended_at = TimeTrackingApplicationService._optional_time(
            snapshot, "actual_ended_at"
        )
        deduction.effective_started_at = TimeTrackingApplicationService._optional_time(
            snapshot, "effective_started_at"
        )
        deduction.effective_ended_at = TimeTrackingApplicationService._optional_time(
            snapshot, "effective_ended_at"
        )
        deduction.source = EntrySource(str(snapshot["source"]))
        deduction.deleted_at = TimeTrackingApplicationService._optional_time(snapshot, "deleted_at")
        deduction.rounding_minutes = int(cast(int, snapshot["rounding_minutes"]))
        deduction.updated_at = now
        deduction.revision += 1

    @staticmethod
    def _optional_time(snapshot: dict[str, object], key: str) -> datetime | None:
        value = snapshot[key]
        return datetime.fromisoformat(str(value)) if value is not None else None

    @staticmethod
    def _session_snapshot(session: WorkSession) -> dict[str, str | int | None]:
        return {
            "actual_started_at": session.actual_started_at.isoformat(),
            "actual_ended_at": session.actual_ended_at.isoformat()
            if session.actual_ended_at
            else None,
            "effective_started_at": session.effective_started_at.isoformat()
            if session.effective_started_at
            else None,
            "effective_ended_at": session.effective_ended_at.isoformat()
            if session.effective_ended_at
            else None,
            "source": session.source.value,
            "deleted_at": session.deleted_at.isoformat() if session.deleted_at else None,
            "rounding_minutes": session.rounding_minutes,
        }

    @staticmethod
    def _deduction_snapshot(deduction: Deduction) -> dict[str, str | int | None]:
        return {
            "actual_started_at": deduction.actual_started_at.isoformat(),
            "actual_ended_at": deduction.actual_ended_at.isoformat()
            if deduction.actual_ended_at
            else None,
            "effective_started_at": deduction.effective_started_at.isoformat()
            if deduction.effective_started_at
            else None,
            "effective_ended_at": deduction.effective_ended_at.isoformat()
            if deduction.effective_ended_at
            else None,
            "source": deduction.source.value,
            "deleted_at": deduction.deleted_at.isoformat() if deduction.deleted_at else None,
            "rounding_minutes": deduction.rounding_minutes,
        }

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

    def _ensure_no_pending_sleep_gap(self) -> None:
        if self.pending_sleep_gap() is not None:
            raise RecoveryRequiredError(
                "Resolve the detected sleep interval before changing the timer."
            )

    @staticmethod
    def _ensure_not_blocked(session: WorkSession, now: datetime) -> None:
        if began_on_previous_local_day(session, now) and session.recovery_acknowledged_at is None:
            raise RecoveryRequiredError("Resolve the unfinished previous-day session first.")

    def _when(self, supplied: datetime | None) -> datetime:
        value = supplied or self._clock.now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must be timezone-aware")
        return value.astimezone(UTC)
