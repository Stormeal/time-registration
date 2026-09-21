"""Concrete use cases for timer-based work and lunch tracking."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import cast

from qi_flow.application.dto import (
    ActiveStateView,
    AppPreferencesView,
    DaySummaryView,
    FinishDeductionCommand,
    FinishWorkCommand,
    ManualDeductionCommand,
    ManualWorkSessionCommand,
    RecoveryView,
    ReminderSettingsView,
    ReminderView,
    SleepGapView,
    StartDeductionCommand,
    StartWorkCommand,
    UpdateActiveWorkStartCommand,
    UpdateDayDetailsCommand,
    UpdateDeductionCommand,
    UpdateWorkSessionCommand,
    WeeklyProgressView,
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
    IsoWeek,
    SessionId,
    WeeklyTarget,
    WorkSession,
)
from qi_flow.domain.time_rules import (
    COPENHAGEN,
    VALID_ROUNDING_MINUTES,
    began_on_previous_local_day,
    effective_interval,
    effective_work_interval,
    net_seconds,
    split_at_local_midnight,
)


@dataclass(frozen=True, slots=True)
class _UndoAction:
    kind: str
    session_id: str
    occurred_at: datetime


@dataclass(slots=True)
class _DayTotal:
    first_start: datetime | None = None
    final_finish: datetime | None = None
    session_count: int = 0
    lunch_seconds: int = 0
    break_seconds: int = 0
    net_seconds: int = 0
    is_provisional: bool = False


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

    def update_active_work_start(self, command: UpdateActiveWorkStartCommand) -> WorkSession:
        """Correct a running session's start without stopping its timer."""
        start = self._when(command.started_at)
        now = self._when(None)
        if start >= now:
            raise InvalidIntervalError("The corrected start time must be before now.")
        with self._uow_factory() as uow:
            session = uow.sessions.get(command.session_id)
            if session is None or session.deleted_at is not None or not session.is_active:
                raise InvalidStateTransitionError("Choose the running work session to correct.")
            self._ensure_session_has_no_overlap(uow, start, now, session.id)
            deductions = uow.deductions.list_for_session(session.id)
            if any(
                deduction.deleted_at is None and deduction.actual_started_at < start
                for deduction in deductions
            ):
                raise InvalidIntervalError(
                    "The work session must still contain all of its lunches and breaks."
                )
            self._record_session_audit(uow, session, "update", now)
            session.actual_started_at = start
            session.effective_started_at = None
            session.effective_ended_at = None
            session.source = EntrySource.MANUAL
            session.rounding_minutes = 1
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

    def completed_sessions_for_day(self, work_date: date) -> list[WorkSession]:
        """Return completed sessions intersecting a Copenhagen calendar day."""
        start = datetime.combine(work_date, datetime.min.time(), COPENHAGEN).astimezone(UTC)
        end = start + timedelta(days=1)
        with self._uow_factory() as uow:
            sessions = uow.sessions.list_intersecting(start, end)
        return [session for session in sessions if session.actual_ended_at is not None]

    def active_session_for_day(self, work_date: date) -> WorkSession | None:
        """Return the running session when it started on this Copenhagen calendar day."""
        with self._uow_factory() as uow:
            session = uow.sessions.get_active()
        if session is None or session.actual_started_at.astimezone(COPENHAGEN).date() != work_date:
            return None
        return session

    def completed_deductions(self, session_id: SessionId) -> list[Deduction]:
        """Return completed, non-deleted deductions for the correction editor."""
        with self._uow_factory() as uow:
            return [
                deduction
                for deduction in uow.deductions.list_for_session(session_id)
                if deduction.actual_ended_at is not None and deduction.deleted_at is None
            ]

    def month(self, year: int, month: int) -> list[DaySummaryView]:
        """Summarize every local calendar day in a month using effective timer values."""
        month_start = date(year, month, 1)
        month_end = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
        return self._summaries_for_range(month_start, month_end)

    def summaries_for_range(self, start_date: date, end_date: date) -> list[DaySummaryView]:
        """Return one rounded, local-calendar summary per day in ``[start, end)``."""
        if end_date <= start_date:
            raise ValueError("The export end date must be after its start date.")
        return self._summaries_for_range(start_date, end_date)

    def history_range(self) -> tuple[date, date]:
        """Find the bounded calendar range used for an all-history export."""
        sessions = self.completed_sessions()
        today = self._when(None).astimezone(COPENHAGEN).date()
        if not sessions:
            return today, today + timedelta(days=1)
        starts = [
            (session.effective_started_at or session.actual_started_at)
            .astimezone(COPENHAGEN)
            .date()
            for session in sessions
        ]
        ends = [
            (session.effective_ended_at or session.actual_ended_at).astimezone(COPENHAGEN).date()
            for session in sessions
            if session.actual_ended_at is not None
        ]
        return min(starts), max(ends) + timedelta(days=1)

    def weekly_progress(self, iso_week: IsoWeek) -> WeeklyProgressView:
        monday = date.fromisocalendar(iso_week.year, iso_week.week, 1)
        summaries = self._summaries_for_range(monday, monday + timedelta(days=7))
        with self._uow_factory() as uow:
            saved = uow.weekly_targets.get(iso_week)
            configured_target = uow.settings.get("default_weekly_target_minutes")
        target_minutes = (
            saved.target_minutes
            if saved is not None
            else configured_target
            if isinstance(configured_target, int) and configured_target >= 0
            else 37 * 60
        )
        return WeeklyProgressView(
            iso_week, sum(summary.net_seconds for summary in summaries), target_minutes
        )

    def set_weekly_target(self, iso_week: IsoWeek, target_minutes: int) -> WeeklyProgressView:
        if target_minutes < 0:
            raise ValueError("Weekly target cannot be negative.")
        with self._uow_factory() as uow:
            uow.weekly_targets.save(WeeklyTarget(iso_week, target_minutes), self._when(None))
        return self.weekly_progress(iso_week)

    def app_preferences(self) -> AppPreferencesView:
        """Read setup/settings values with the agreed first-run defaults."""
        with self._uow_factory() as uow:
            target = uow.settings.get("default_weekly_target_minutes")
            sleep_enabled = uow.settings.get("sleep_detection_enabled")
            sleep_minutes = uow.settings.get("sleep_threshold_minutes")
            theme = uow.settings.get("theme")
        return AppPreferencesView(
            rounding_minutes=self.rounding_minutes,
            weekly_target_minutes=target if isinstance(target, int) and target >= 0 else 37 * 60,
            sleep_enabled=sleep_enabled is not False,
            sleep_threshold_minutes=sleep_minutes
            if isinstance(sleep_minutes, int) and sleep_minutes > 0
            else 30,
            theme=theme if theme in {"system", "light", "dark"} else "system",
        )

    def save_app_preferences(self, preferences: AppPreferencesView) -> None:
        if preferences.rounding_minutes not in VALID_ROUNDING_MINUTES:
            raise ValueError("rounding must be one of 1, 5, 10, or 15 minutes")
        if preferences.weekly_target_minutes < 0 or preferences.sleep_threshold_minutes < 1:
            raise ValueError("Target and sleep threshold must be valid positive values.")
        if preferences.theme not in {"system", "light", "dark"}:
            raise ValueError("Theme must be system, light, or dark.")
        now = self._when(None)
        self._rounding_minutes = preferences.rounding_minutes
        with self._uow_factory() as uow:
            uow.settings.save("rounding_minutes", preferences.rounding_minutes, now)
            uow.settings.save(
                "default_weekly_target_minutes", preferences.weekly_target_minutes, now
            )
            uow.settings.save("sleep_detection_enabled", preferences.sleep_enabled, now)
            uow.settings.save("sleep_threshold_minutes", preferences.sleep_threshold_minutes, now)
            uow.settings.save("theme", preferences.theme, now)

    def setup_complete(self) -> bool:
        with self._uow_factory() as uow:
            return uow.settings.get("setup_completed") is True

    def complete_setup(self) -> None:
        with self._uow_factory() as uow:
            uow.settings.save("setup_completed", True, self._when(None))

    def reminder_settings(self) -> ReminderSettingsView:
        with self._uow_factory() as uow:
            work_enabled = uow.settings.get("work_reminder_enabled")
            work_minutes = uow.settings.get("work_reminder_minutes")
            lunch_enabled = uow.settings.get("lunch_reminder_enabled")
            lunch_minutes = uow.settings.get("lunch_reminder_minutes")
        return ReminderSettingsView(
            work_enabled=work_enabled is not False,
            work_minutes=work_minutes
            if isinstance(work_minutes, int) and work_minutes > 0
            else 9 * 60,
            lunch_enabled=lunch_enabled is not False,
            lunch_minutes=lunch_minutes
            if isinstance(lunch_minutes, int) and lunch_minutes > 0
            else 45,
        )

    def set_reminder_settings(self, settings: ReminderSettingsView) -> None:
        if settings.work_minutes < 1 or settings.lunch_minutes < 1:
            raise ValueError("Reminder thresholds must be at least one minute.")
        now = self._when(None)
        with self._uow_factory() as uow:
            uow.settings.save("work_reminder_enabled", settings.work_enabled, now)
            uow.settings.save("work_reminder_minutes", settings.work_minutes, now)
            uow.settings.save("lunch_reminder_enabled", settings.lunch_enabled, now)
            uow.settings.save("lunch_reminder_minutes", settings.lunch_minutes, now)

    def due_reminders(self) -> list[ReminderView]:
        """Return each unsnoozed threshold crossing once for the active session."""
        now = self._when(None)
        settings = self.reminder_settings()
        due: list[ReminderView] = []
        with self._uow_factory() as uow:
            session = uow.sessions.get_active()
            if session is None:
                return due
            deductions = uow.deductions.list_for_session(session.id)
            state_net_seconds = net_seconds(session, deductions, now)
            elapsed_seconds = int((now - session.actual_started_at).total_seconds())
            active_deduction = uow.deductions.get_active(session.id)
            candidates: list[tuple[str, int, bool]] = [
                ("work", settings.work_minutes * 60, settings.work_enabled)
            ]
            if active_deduction is not None and active_deduction.kind is DeductionKind.LUNCH:
                candidates.append(("lunch", settings.lunch_minutes * 60, settings.lunch_enabled))
            for kind, threshold, enabled in candidates:
                duration = (
                    int((now - active_deduction.actual_started_at).total_seconds())
                    if kind == "lunch" and active_deduction is not None
                    else elapsed_seconds
                )
                if (
                    not enabled
                    or duration < threshold
                    or self._reminder_is_suppressed(uow, kind, session.id, now)
                ):
                    continue
                uow.settings.save(f"reminder_notified_{kind}", {"session_id": str(session.id)}, now)
                due.append(ReminderView(kind, duration, state_net_seconds))
        return due

    def snooze_reminder(self, kind: str, minutes: int) -> None:
        if kind not in {"work", "lunch"} or minutes not in {15, 30, 60}:
            raise ValueError("Reminder snooze must be 15, 30, or 60 minutes.")
        now = self._when(None)
        with self._uow_factory() as uow:
            session = uow.sessions.get_active()
            if session is None:
                return
            uow.settings.save(
                f"reminder_snooze_{kind}",
                {
                    "session_id": str(session.id),
                    "until": (now + timedelta(minutes=minutes)).isoformat(),
                },
                now,
            )
            uow.settings.save(f"reminder_notified_{kind}", None, now)

    @staticmethod
    def _reminder_is_suppressed(
        uow: UnitOfWork, kind: str, session_id: SessionId, now: datetime
    ) -> bool:
        snooze = uow.settings.get(f"reminder_snooze_{kind}")
        if isinstance(snooze, dict) and snooze.get("session_id") == str(session_id):
            try:
                if datetime.fromisoformat(str(snooze["until"])) > now:
                    return True
            except (KeyError, ValueError):
                pass
        notified = uow.settings.get(f"reminder_notified_{kind}")
        return isinstance(notified, dict) and notified.get("session_id") == str(session_id)

    def _summaries_for_range(self, start_date: date, end_date: date) -> list[DaySummaryView]:
        start = datetime.combine(start_date, datetime.min.time(), COPENHAGEN).astimezone(UTC)
        end = datetime.combine(end_date, datetime.min.time(), COPENHAGEN).astimezone(UTC)
        now = self._when(None)
        totals: dict[date, _DayTotal] = {
            current: _DayTotal()
            for current in (
                start_date + timedelta(days=offset)
                for offset in range((end_date - start_date).days)
            )
        }
        with self._uow_factory() as uow:
            sessions = uow.sessions.list_intersecting(start, end)
            details = {work_date: uow.days.get(work_date) for work_date in totals}
            for session in sessions:
                session_start = session.effective_started_at or session.actual_started_at
                session_end = session.effective_ended_at or session.actual_ended_at or now
                if session_end <= start or session_start >= end:
                    continue
                deductions = uow.deductions.list_for_session(session.id)
                for piece_start, piece_end in split_at_local_midnight(
                    max(session_start, start), min(session_end, end)
                ):
                    work_date = piece_start.astimezone(COPENHAGEN).date()
                    total = totals[work_date]
                    total.session_count += 1
                    total.first_start = self._earlier(total.first_start, piece_start)
                    total.final_finish = self._later(total.final_finish, piece_end)
                    gross = int((piece_end - piece_start).total_seconds())
                    excluded = 0
                    for deduction in deductions:
                        if deduction.deleted_at is not None:
                            continue
                        deduction_start = (
                            deduction.effective_started_at or deduction.actual_started_at
                        )
                        deduction_end = (
                            deduction.effective_ended_at or deduction.actual_ended_at or now
                        )
                        overlap_start = max(piece_start, deduction_start)
                        overlap_end = min(piece_end, deduction_end)
                        if overlap_end <= overlap_start:
                            continue
                        seconds = int((overlap_end - overlap_start).total_seconds())
                        excluded += seconds
                        field = (
                            "lunch_seconds"
                            if deduction.kind is DeductionKind.LUNCH
                            else "break_seconds"
                        )
                        if field == "lunch_seconds":
                            total.lunch_seconds += seconds
                        else:
                            total.break_seconds += seconds
                    total.net_seconds += gross - excluded
                    if session.actual_ended_at is None:
                        total.is_provisional = True
        summaries: list[DaySummaryView] = []
        for work_date, total in totals.items():
            detail = details[work_date]
            summaries.append(
                DaySummaryView(
                    work_date=work_date,
                    first_start=total.first_start,
                    final_finish=total.final_finish,
                    session_count=total.session_count,
                    lunch_seconds=total.lunch_seconds,
                    break_seconds=total.break_seconds,
                    net_seconds=total.net_seconds,
                    location=detail.location if detail is not None else None,
                    has_note=bool(detail.note) if detail is not None else False,
                    is_provisional=total.is_provisional,
                )
            )
        return summaries

    @staticmethod
    def _earlier(current: datetime | None, candidate: datetime) -> datetime:
        return candidate if current is None or candidate < current else current

    @staticmethod
    def _later(current: datetime | None, candidate: datetime) -> datetime:
        return candidate if current is None or candidate > current else current

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
            try:
                start, end = effective_interval(
                    deduction.actual_started_at, now, deduction.rounding_minutes
                )
            except InvalidIntervalError:
                # A timer-created deduction can be shorter than the selected rounding
                # interval.  Its actual boundaries are still a valid, known duration;
                # retain them rather than trapping the user in an active lunch or
                # manufacturing a rounded minute.
                start, end = deduction.actual_started_at, now
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
        start, end = effective_work_interval(
            session.actual_started_at, now, session.rounding_minutes
        )
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
        task_id = snapshot.get("testhuset_task_id")
        session.testhuset_task_id = str(task_id) if task_id is not None else None
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
            "testhuset_task_id": session.testhuset_task_id,
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
