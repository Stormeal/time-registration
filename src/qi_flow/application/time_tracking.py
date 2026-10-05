"""Concrete use cases for timer-based work and lunch tracking."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import cast

from qi_flow.application.dto import (
    ActiveStateView,
    AppPreferencesView,
    DaySummaryView,
    EntryHistoryView,
    FinishDeductionCommand,
    FinishWorkCommand,
    ManualDeductionCommand,
    ManualWorkSessionCommand,
    RecoveryView,
    ReminderSettingsView,
    ReminderView,
    SleepGapView,
    StartDeductionCommand,
    StartWorkAtCommand,
    StartWorkCommand,
    UpdateActiveWorkStartCommand,
    UpdateDayDetailsCommand,
    UpdateDeductionCommand,
    UpdateWorkSessionCommand,
    WeeklyProgressView,
)
from qi_flow.application.ports import Clock, IdentifierGenerator, UnitOfWork
from qi_flow.application.sync_capture import captured_mutation
from qi_flow.domain.errors import (
    InvalidIntervalError,
    InvalidStateTransitionError,
    OverlappingIntervalError,
    RecoveryRequiredError,
)
from qi_flow.domain.interval_validation import validate_intervals
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
    local_day_bounds,
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
        *,
        on_work_finished: Callable[[], None] | None = None,
    ) -> None:
        if rounding_minutes not in VALID_ROUNDING_MINUTES:
            raise ValueError("rounding must be one of 1, 5, 10, or 15 minutes")
        self._uow_factory = uow_factory
        self._clock = clock
        self._identifiers = identifiers
        self._rounding_minutes = rounding_minutes
        self._undo_action: _UndoAction | None = None
        self._on_work_finished = on_work_finished

    def _mutation(self, *, finish_grace: bool = False) -> AbstractContextManager[UnitOfWork]:
        now = self._when(None)
        return captured_mutation(
            self._uow_factory,
            self._identifiers,
            now,
            not_before=now + timedelta(seconds=30) if finish_grace else None,
        )

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
        with self._mutation() as uow:
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
            self._copy_office_context(uow, start, end)
        return session

    def update_work_session(self, command: UpdateWorkSessionCommand) -> WorkSession:
        start, end = self._completed_times(command.started_at, command.ended_at)
        now = self._when(None)
        with self._mutation() as uow:
            session = uow.sessions.get(command.session_id)
            if session is None or session.deleted_at is not None or session.is_active:
                raise InvalidStateTransitionError("Choose a completed work session to edit.")
            candidate = replace(
                session,
                actual_started_at=start,
                actual_ended_at=end,
                effective_started_at=start,
                effective_ended_at=end,
                source=EntrySource.MANUAL,
                updated_at=now,
                revision=session.revision + 1,
            )
            self._validate_candidate(uow, now, session=candidate)
            self._record_session_audit(uow, session, "update", now)
            session = candidate
            uow.sessions.save(session)
            self._copy_office_context(uow, start, end)
        return session

    def update_active_work_start(self, command: UpdateActiveWorkStartCommand) -> WorkSession:
        """Correct a running session's start without stopping its timer."""
        self._ensure_no_pending_sleep_gap()
        start = self._when(command.started_at)
        now = self._when(None)
        if start >= now:
            raise InvalidIntervalError("The corrected start time must be before now.")
        with self._mutation() as uow:
            session = uow.sessions.get(command.session_id)
            if session is None or session.deleted_at is not None or not session.is_active:
                raise InvalidStateTransitionError("Choose the running work session to correct.")
            self._ensure_not_blocked(session, now)
            candidate = replace(
                session,
                actual_started_at=start,
                effective_started_at=None,
                effective_ended_at=None,
                source=EntrySource.MANUAL,
                rounding_minutes=1,
                updated_at=now,
                revision=session.revision + 1,
            )
            self._validate_candidate(uow, now, session=candidate)
            self._record_session_audit(uow, session, "update", now)
            session = candidate
            uow.sessions.save(session)
        return session

    def add_manual_deduction(self, command: ManualDeductionCommand) -> Deduction:
        start, end = self._completed_times(command.started_at, command.ended_at)
        now = self._when(None)
        with self._mutation() as uow:
            session = uow.sessions.get(command.session_id)
            if session is None or session.deleted_at is not None:
                raise InvalidStateTransitionError(
                    "A manual lunch or break needs an available work session."
                )
            if session.actual_ended_at is None and not session.is_active:
                raise InvalidStateTransitionError("The work session is unavailable.")
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
        with self._mutation() as uow:
            deduction = uow.deductions.get(command.deduction_id)
            if deduction is None or deduction.deleted_at is not None or deduction.is_active:
                raise InvalidStateTransitionError("Choose a completed lunch or break to edit.")
            session = uow.sessions.get(deduction.session_id)
            if session is None or session.deleted_at is not None:
                raise InvalidStateTransitionError("The parent work session is unavailable.")
            if session.actual_ended_at is None and not session.is_active:
                raise InvalidStateTransitionError("The parent work session is unavailable.")
            candidate = replace(
                deduction,
                actual_started_at=start,
                actual_ended_at=end,
                effective_started_at=start,
                effective_ended_at=end,
                source=EntrySource.MANUAL,
                updated_at=now,
                revision=deduction.revision + 1,
            )
            self._validate_candidate(uow, now, deduction=candidate)
            self._record_deduction_audit(uow, deduction, "update", now)
            deduction = candidate
            uow.deductions.save(deduction)
        return deduction

    def delete_work_session(self, session_id: SessionId) -> None:
        now = self._when(None)
        with self._mutation() as uow:
            session = uow.sessions.get(session_id)
            if session is None or session.deleted_at is not None:
                raise InvalidStateTransitionError("That work session no longer exists.")
            self._record_session_audit(uow, session, "delete", now)
            session.deleted_at = now
            session.updated_at = now
            session.revision += 1
            uow.sessions.save(session)
            self._retire_reminder(uow, "work", str(session.id), now)
            for deduction in uow.deductions.list_for_session(session.id):
                if deduction.deleted_at is None:
                    self._record_deduction_audit(uow, deduction, "delete", now)
                    deduction.deleted_at = now
                    deduction.updated_at = now
                    deduction.revision += 1
                    uow.deductions.save(deduction)
                    if deduction.kind is DeductionKind.LUNCH:
                        self._retire_reminder(uow, "lunch", str(deduction.id), now)

    def delete_deduction(self, deduction_id: DeductionId) -> None:
        now = self._when(None)
        with self._mutation() as uow:
            deduction = uow.deductions.get(deduction_id)
            if deduction is None or deduction.deleted_at is not None:
                raise InvalidStateTransitionError("That lunch or break no longer exists.")
            self._record_deduction_audit(uow, deduction, "delete", now)
            deduction.deleted_at = now
            deduction.updated_at = now
            deduction.revision += 1
            uow.deductions.save(deduction)
            if deduction.kind is DeductionKind.LUNCH:
                self._retire_reminder(uow, "lunch", str(deduction.id), now)

    def restore_work_session(self, session_id: SessionId) -> WorkSession:
        now = self._when(None)
        with self._mutation() as uow:
            session = uow.sessions.get(session_id)
            snapshot = uow.audit.latest("work_session", str(session_id))
            if session is None or snapshot is None:
                raise InvalidStateTransitionError(
                    "No recoverable work-session version is available."
                )
            candidate = replace(session)
            self._restore_session_snapshot(candidate, snapshot, now)
            self._validate_candidate(uow, now, session=candidate)
            self._record_session_audit(uow, session, "update", now)
            session = candidate
            uow.sessions.save(session)
        return session

    def restore_deduction(self, deduction_id: DeductionId) -> Deduction:
        now = self._when(None)
        with self._mutation() as uow:
            deduction = uow.deductions.get(deduction_id)
            snapshot = uow.audit.latest("deduction", str(deduction_id))
            if deduction is None or snapshot is None:
                raise InvalidStateTransitionError(
                    "No recoverable lunch or break version is available."
                )
            candidate = replace(deduction)
            self._restore_deduction_snapshot(candidate, snapshot, now)
            self._validate_candidate(uow, now, deduction=candidate)
            self._record_deduction_audit(uow, deduction, "update", now)
            deduction = candidate
            uow.deductions.save(deduction)
        return deduction

    def entry_history_for_day(self, work_date: date) -> list[EntryHistoryView]:
        """List unexpired work and deduction before-images intersecting a local date."""
        start = datetime.combine(work_date, datetime.min.time(), COPENHAGEN).astimezone(UTC)
        end = datetime.combine(work_date + timedelta(days=1), datetime.min.time(), COPENHAGEN)
        end = end.astimezone(UTC)
        now = self._when(None)
        result: list[EntryHistoryView] = []
        with self._uow_factory() as uow:
            for row in uow.audit.list_active(now):
                if row["entity_type"] not in {"work_session", "deduction"}:
                    continue
                snapshot = cast(dict[str, object], row["before_state"])
                start_value = snapshot.get("actual_started_at")
                if not isinstance(start_value, str):
                    continue
                interval_start = datetime.fromisoformat(start_value).astimezone(UTC)
                end_value = snapshot.get("actual_ended_at")
                interval_end = (
                    datetime.fromisoformat(end_value).astimezone(UTC)
                    if isinstance(end_value, str)
                    else now
                )
                if interval_start >= end or interval_end <= start:
                    continue
                result.append(
                    EntryHistoryView(
                        str(row["audit_id"]),
                        str(row["entity_type"]),
                        str(row["entity_id"]),
                        str(row["action"]),
                        cast(datetime, row["changed_at"]),
                        snapshot,
                    )
                )
        return result

    def restore_history_entry(self, audit_id: str) -> WorkSession | Deduction:
        """Restore the exact before-image selected from an entry's recoverable history."""
        now = self._when(None)
        with self._mutation() as uow:
            row = uow.audit.get_active(audit_id, now)
            if row is None:
                raise InvalidStateTransitionError(
                    "That history version has expired or is no longer available."
                )
            entity_type = str(row["entity_type"])
            entity_id = str(row["entity_id"])
            snapshot = cast(dict[str, object], row["before_state"])
            if entity_type == "work_session":
                session_id = SessionId(entity_id)
                session = uow.sessions.get(session_id)
                if session is None:
                    raise InvalidStateTransitionError("The work session is unavailable.")
                session_candidate = replace(session)
                self._restore_session_snapshot(session_candidate, snapshot, now)
                self._validate_candidate(uow, now, session=session_candidate)
                self._record_session_audit(uow, session, "update", now)
                session = session_candidate
                uow.sessions.save(session)
                return session
            if entity_type == "deduction":
                deduction_id = DeductionId(entity_id)
                deduction = uow.deductions.get(deduction_id)
                parent_session = uow.sessions.get(deduction.session_id) if deduction else None
                if (
                    deduction is None
                    or parent_session is None
                    or parent_session.deleted_at is not None
                ):
                    raise InvalidStateTransitionError(
                        "The parent work session is unavailable. Restore it first."
                    )
                deduction_candidate = replace(deduction)
                self._restore_deduction_snapshot(deduction_candidate, snapshot, now)
                self._validate_candidate(uow, now, deduction=deduction_candidate)
                self._record_deduction_audit(uow, deduction, "update", now)
                deduction = deduction_candidate
                uow.deductions.save(deduction)
                return deduction
            raise InvalidStateTransitionError("This history record cannot be restored here.")

    def update_day_details(self, command: UpdateDayDetailsCommand) -> DaySummaryView:
        with self._mutation() as uow:
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
        start, end = local_day_bounds(work_date)
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

    def today_summary(self) -> DaySummaryView:
        """Return today's effective allocated total using the injected clock."""
        today = self._when(None).astimezone(COPENHAGEN).date()
        return self._summaries_for_range(today, today + timedelta(days=1))[0]

    def active_lunch_seconds(self) -> int:
        """Expose actual lunch elapsed time from the same injected clock as work."""
        now = self._when(None)
        state = self.active_state(now)
        if state.active_deduction_kind is not DeductionKind.LUNCH:
            return 0
        start = state.actual_deduction_started_at
        return max(0, int((now - start).total_seconds())) if start is not None else 0

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
        """Return each unsnoozed threshold crossing once for its active timer."""
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
                subject_id = (
                    str(active_deduction.id)
                    if kind == "lunch" and active_deduction is not None
                    else str(session.id)
                )
                duration = (
                    int((now - active_deduction.actual_started_at).total_seconds())
                    if kind == "lunch" and active_deduction is not None
                    else elapsed_seconds
                )
                if (
                    not enabled
                    or duration < threshold
                    or self._reminder_is_suppressed(uow, kind, subject_id, now)
                ):
                    continue
                subject_key = "deduction_id" if kind == "lunch" else "session_id"
                uow.settings.save(f"reminder_notified_{kind}", {subject_key: subject_id}, now)
                due.append(ReminderView(kind, duration, state_net_seconds, subject_id))
        return due

    def snooze_reminder(self, kind: str, minutes: int, *, subject_id: str | None = None) -> None:
        if kind not in {"work", "lunch"} or minutes not in {15, 30, 60}:
            raise ValueError("Reminder snooze must be 15, 30, or 60 minutes.")
        now = self._when(None)
        with self._uow_factory() as uow:
            session = uow.sessions.get_active()
            if session is None:
                return
            deduction = uow.deductions.get_active(session.id) if kind == "lunch" else None
            if kind == "lunch" and (deduction is None or deduction.kind is not DeductionKind.LUNCH):
                return
            subject_key = "deduction_id" if kind == "lunch" else "session_id"
            current_id = str(deduction.id) if deduction is not None else str(session.id)
            if subject_id is not None and subject_id != current_id:
                return
            uow.settings.save(
                f"reminder_snooze_{kind}",
                {
                    subject_key: current_id,
                    "until": (now + timedelta(minutes=minutes)).isoformat(),
                },
                now,
            )
            uow.settings.save(f"reminder_notified_{kind}", None, now)

    @staticmethod
    def _reminder_is_suppressed(uow: UnitOfWork, kind: str, subject_id: str, now: datetime) -> bool:
        subject_key = "deduction_id" if kind == "lunch" else "session_id"
        snooze = uow.settings.get(f"reminder_snooze_{kind}")
        if isinstance(snooze, dict) and snooze.get(subject_key) == subject_id:
            try:
                if datetime.fromisoformat(str(snooze["until"])) > now:
                    return True
            except (KeyError, ValueError):
                pass
        notified = uow.settings.get(f"reminder_notified_{kind}")
        return isinstance(notified, dict) and notified.get(subject_key) == subject_id

    @staticmethod
    def _retire_reminder(uow: UnitOfWork, kind: str, subject_id: str, now: datetime) -> None:
        subject_key = "deduction_id" if kind == "lunch" else "session_id"
        for state in ("snooze", "notified"):
            key = f"reminder_{state}_{kind}"
            value = uow.settings.get(key)
            if isinstance(value, dict) and value.get(subject_key) == subject_id:
                uow.settings.save(key, None, now)

    def _summaries_for_range(self, start_date: date, end_date: date) -> list[DaySummaryView]:
        start, _ = local_day_bounds(start_date)
        end, _ = local_day_bounds(end_date)
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
        now = self._when(None)
        if threshold == 0 or end <= start or end > now:
            return None
        with self._uow_factory() as uow:
            session = uow.sessions.get_active()
            if session is None or uow.settings.get("pending_sleep_gap") is not None:
                return None
            if start < session.actual_started_at:
                start = session.actual_started_at
            if end - start <= timedelta(seconds=threshold):
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
        with self._mutation() as uow:
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
                covered = sorted(
                    (
                        deduction.actual_started_at,
                        deduction.actual_ended_at or now,
                    )
                    for deduction in uow.deductions.list_for_session(session.id)
                    if deduction.deleted_at is None
                    and deduction.actual_started_at < end
                    and (deduction.actual_ended_at or now) > start
                )
                uncovered: list[tuple[datetime, datetime]] = []
                cursor = start
                for covered_start, covered_end in covered:
                    if covered_start > cursor:
                        uncovered.append((cursor, min(covered_start, end)))
                    cursor = max(cursor, covered_end)
                    if cursor >= end:
                        break
                if cursor < end:
                    uncovered.append((cursor, end))
                for break_start, break_end in uncovered:
                    if break_end <= break_start:
                        continue
                    uow.deductions.add(
                        Deduction(
                            id=self._identifiers.deduction_id(),
                            session_id=session.id,
                            kind=DeductionKind.SLEEP_BREAK,
                            actual_started_at=break_start,
                            actual_ended_at=break_end,
                            effective_started_at=break_start,
                            effective_ended_at=break_end,
                            source=EntrySource.RECOVERY,
                            created_at=now,
                            updated_at=now,
                            rounding_minutes=1,
                        )
                    )
            uow.settings.save("pending_sleep_gap", None, now)
        return self.active_state(now)

    def current_time(self) -> datetime:
        """Expose the injected clock for user-entered start-time defaults."""
        return self._when(None)

    def start_work_at(self, command: StartWorkAtCommand) -> ActiveStateView:
        """Begin exact-minute manual work while retaining a running timer."""
        start = self._when(command.started_at)
        now = self._when(None)
        if start > now:
            raise InvalidIntervalError("The start time cannot be in the future.")
        self._ensure_no_pending_sleep_gap()
        with self._mutation() as uow:
            self._ensure_no_blocking_session(uow.sessions.get_active(), now)
            self._ensure_session_has_no_overlap(uow, start, now)
            session = WorkSession(
                id=self._identifiers.session_id(),
                actual_started_at=start,
                source=EntrySource.MANUAL,
                created_at=now,
                updated_at=now,
                rounding_minutes=1,
            )
            uow.sessions.add(session)
        self._undo_action = _UndoAction("start", str(session.id), now)
        return self.active_state(now)

    def start_work(self, command: StartWorkCommand) -> ActiveStateView:
        now = self._when(command.occurred_at)
        rounding_minutes = self.rounding_minutes
        with self._mutation() as uow:
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
        with self._mutation(finish_grace=True) as uow:
            session = self._require_active(uow)
            if uow.deductions.get_active(session.id) is not None:
                raise InvalidStateTransitionError("End lunch before finishing work.")
            self._complete_session(session, now)
            uow.sessions.save(session)
            self._retire_reminder(uow, "work", str(session.id), now)
            self._copy_office_context(uow, session.actual_started_at, now)
        self._undo_action = _UndoAction("finish", str(session.id), now)
        if self._on_work_finished is not None:
            self._on_work_finished()
        return ActiveStateView(None, None, None, None, 0)

    def start_deduction(self, command: StartDeductionCommand) -> ActiveStateView:
        now = self._when(command.occurred_at)
        self._ensure_no_pending_sleep_gap()
        rounding_minutes = self.rounding_minutes
        with self._mutation() as uow:
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
        self._undo_action = _UndoAction("start_deduction", str(deduction.id), now)
        return self.active_state(now)

    def finish_deduction(self, command: FinishDeductionCommand) -> ActiveStateView:
        now = self._when(command.occurred_at)
        self._ensure_no_pending_sleep_gap()
        with self._mutation() as uow:
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
            if deduction.kind is DeductionKind.LUNCH:
                self._retire_reminder(uow, "lunch", str(deduction.id), now)
        self._undo_action = _UndoAction("finish_deduction", str(deduction.id), now)
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
        with self._mutation() as uow:
            if action.kind in {"start_deduction", "finish_deduction"}:
                deduction = uow.deductions.get(DeductionId(action.session_id))
                if deduction is None:
                    raise InvalidStateTransitionError("The timer action can no longer be undone.")
                session = uow.sessions.get(deduction.session_id)
                if session is None or not session.is_active:
                    raise InvalidStateTransitionError("The timer action can no longer be undone.")
                if action.kind == "start_deduction":
                    if not deduction.is_active:
                        raise InvalidStateTransitionError(
                            "The timer action can no longer be undone."
                        )
                    deduction.deleted_at = now
                else:
                    if deduction.actual_ended_at is None or (
                        uow.deductions.get_active(session.id) is not None
                    ):
                        raise InvalidStateTransitionError(
                            "The timer action can no longer be undone."
                        )
                    deduction.actual_ended_at = None
                    deduction.effective_started_at = None
                    deduction.effective_ended_at = None
                deduction.updated_at = now
                deduction.revision += 1
                uow.deductions.save(deduction)
                if action.kind == "start_deduction" and deduction.kind is DeductionKind.LUNCH:
                    self._retire_reminder(uow, "lunch", str(deduction.id), now)
            else:
                session = uow.sessions.get(SessionId(action.session_id))
                if session is None:
                    raise InvalidStateTransitionError("The timer action can no longer be undone.")
                if action.kind == "start":
                    if not session.is_active or uow.deductions.get_active(session.id) is not None:
                        raise InvalidStateTransitionError(
                            "The timer action can no longer be undone."
                        )
                    session.deleted_at = now
                else:
                    if uow.sessions.get_active() is not None:
                        raise InvalidStateTransitionError(
                            "The timer action can no longer be undone."
                        )
                    session.actual_ended_at = None
                    session.effective_started_at = None
                    session.effective_ended_at = None
                session.updated_at = now
                session.revision += 1
                uow.sessions.save(session)
                if action.kind == "start":
                    self._retire_reminder(uow, "work", str(session.id), now)
        self._undo_action = None
        return self.active_state(now)

    def continue_recovery(self) -> ActiveStateView:
        now = self._when(None)
        with self._mutation() as uow:
            session = self._require_active(uow)
            session.recovery_acknowledged_at = now
            session.updated_at = now
            session.revision += 1
            uow.sessions.save(session)
        return self.active_state(now)

    def delete_recovery(self) -> ActiveStateView:
        now = self._when(None)
        with self._mutation() as uow:
            session = self._require_active(uow)
            self._record_session_audit(uow, session, "delete", now)
            session.deleted_at = now
            session.updated_at = now
            session.revision += 1
            uow.sessions.save(session)
            self._retire_reminder(uow, "work", str(session.id), now)
            for deduction in uow.deductions.list_for_session(session.id):
                if deduction.deleted_at is None:
                    self._record_deduction_audit(uow, deduction, "delete", now)
                    deduction.deleted_at = now
                    deduction.updated_at = now
                    deduction.revision += 1
                    uow.deductions.save(deduction)
                    if deduction.kind is DeductionKind.LUNCH:
                        self._retire_reminder(uow, "lunch", str(deduction.id), now)
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

    @staticmethod
    def _copy_office_context(uow: UnitOfWork, started_at: datetime, ended_at: datetime) -> None:
        """Copy the starting day's office choice to later dates the interval touches."""
        local_start = started_at.astimezone(COPENHAGEN).date()
        local_end = ended_at.astimezone(COPENHAGEN).date()
        if local_end <= local_start:
            return
        source = uow.days.get(local_start)
        if source is None:
            return
        day = local_start + timedelta(days=1)
        while day <= local_end:
            if uow.days.get(day) is None:
                uow.days.save(DayDetails(day, source.location, ""))
            day += timedelta(days=1)

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
    def _validate_candidate(
        uow: UnitOfWork,
        now: datetime,
        *,
        session: WorkSession | None = None,
        deduction: Deduction | None = None,
    ) -> None:
        # Legacy sync may have left independent invalid aggregates. Local correction
        # must repair one parent at a time while still refusing candidate conflicts.
        if session is not None:
            conflicts = {
                item.id: item
                for item in uow.sessions.list_intersecting(
                    session.actual_started_at, session.actual_ended_at or now
                )
                if item.id != session.id
            }
            if session.is_active:
                conflicts.update(
                    (item.id, item)
                    for item in uow.sessions.list_all()
                    if item.id != session.id and item.is_active
                )
            validate_intervals(
                [session, *conflicts.values()],
                uow.deductions.list_for_session(session.id),
                as_of=now,
            )
        elif deduction is not None:
            parent = uow.sessions.get(deduction.session_id)
            if parent is None:
                raise InvalidStateTransitionError("Restore the parent work session first.")
            siblings = [
                deduction if item.id == deduction.id else item
                for item in uow.deductions.list_for_session(parent.id)
            ]
            validate_intervals([parent], siblings, as_of=now)

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
        session_end = session.actual_ended_at or end
        if start < session.actual_started_at or end > session_end:
            raise InvalidIntervalError("Lunches and breaks must stay inside their work session.")
        for deduction in uow.deductions.list_for_session(session.id):
            if deduction.id == excluded_id or deduction.deleted_at is not None:
                continue
            deduction_end = deduction.actual_ended_at
            if deduction_end is None:
                if end > deduction.actual_started_at:
                    raise OverlappingIntervalError("Lunches and breaks cannot overlap.")
                continue
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
        try:
            TimeTrackingApplicationService._apply_session_snapshot(session, snapshot, now)
            session.__post_init__()
        except (KeyError, TypeError, ValueError) as error:
            raise InvalidIntervalError(
                "The saved work-session version has invalid fields."
            ) from error

    @staticmethod
    def _apply_session_snapshot(
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
        session.recovery_acknowledged_at = TimeTrackingApplicationService._optional_time(
            snapshot, "recovery_acknowledged_at"
        )
        session.rounding_minutes = int(cast(int, snapshot["rounding_minutes"]))
        task_id = snapshot.get("testhuset_task_id")
        session.testhuset_task_id = str(task_id) if task_id is not None else None
        allocation_id = snapshot.get("dsb_allocation_id")
        session.dsb_allocation_id = str(allocation_id) if allocation_id is not None else None
        session.updated_at = now
        session.revision += 1

    @staticmethod
    def _restore_deduction_snapshot(
        deduction: Deduction, snapshot: dict[str, object], now: datetime
    ) -> None:
        try:
            TimeTrackingApplicationService._apply_deduction_snapshot(deduction, snapshot, now)
            deduction.__post_init__()
        except (KeyError, TypeError, ValueError) as error:
            raise InvalidIntervalError(
                "The saved lunch or break version has invalid fields."
            ) from error

    @staticmethod
    def _apply_deduction_snapshot(
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
        kind = snapshot.get("kind")
        if kind is not None:
            deduction.kind = DeductionKind(str(kind))
        deduction.deleted_at = TimeTrackingApplicationService._optional_time(snapshot, "deleted_at")
        deduction.rounding_minutes = int(cast(int, snapshot["rounding_minutes"]))
        deduction.updated_at = now
        deduction.revision += 1

    @staticmethod
    def _optional_time(snapshot: dict[str, object], key: str) -> datetime | None:
        value = snapshot.get(key)
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
            "recovery_acknowledged_at": session.recovery_acknowledged_at.isoformat()
            if session.recovery_acknowledged_at
            else None,
            "rounding_minutes": session.rounding_minutes,
            "testhuset_task_id": session.testhuset_task_id,
            "dsb_allocation_id": session.dsb_allocation_id,
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
            "kind": deduction.kind.value,
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
