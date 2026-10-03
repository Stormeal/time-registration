"""Conflict-safe synchronization of completed records through a gateway."""

import hashlib
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import Any, Protocol

from qi_flow.application.ports import Clock, IdentifierGenerator, SyncGateway, UnitOfWork
from qi_flow.application.sync_capture import active_target
from qi_flow.application.sync_models import (
    EntityKey,
    SyncChange,
    SyncConflictReview,
    SyncContentError,
    SyncJobCancelledError,
    SyncJobObsoleteError,
    SyncProblem,
    SyncReviewError,
    SyncTarget,
    canonical_json,
    finalize_group,
    utc_instant,
    validate_group,
)
from qi_flow.application.sync_payloads import day_payload, deduction_payload, session_payload
from qi_flow.application.sync_reconciliation import SyncReconciler, validate_completed_parents
from qi_flow.domain.interval_validation import validate_intervals
from qi_flow.domain.models import (
    DayDetails,
    Deduction,
    DeductionId,
    DeductionKind,
    EntrySource,
    SessionId,
    WorkLocation,
    WorkSession,
)


class GoogleSyncGateway(Protocol):
    def read_records(self) -> list[dict[str, Any]]: ...

    def replace_records(self, records: list[dict[str, Any]]) -> int: ...


class GoogleSyncConflictError(ValueError):
    """Raised when two machines changed the same revision differently."""


class GoogleSyncUpgradeRequiredError(ValueError):
    """Legacy snapshot synchronization is contained until reviewed V2 migration."""


class SyncPublicationService:
    """Target-bound publication phase; reconciliation/migration own eligibility first."""

    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        gateway: SyncGateway,
        target: SyncTarget,
        clock: Clock,
        *,
        generation: int,
    ) -> None:
        self._factory, self._gateway = uow_factory, gateway
        self._target, self._clock, self._generation = target, clock, generation

    def _ensure_binding(self, uow: UnitOfWork) -> None:
        if (
            uow.settings.get("google_sync_generation") != self._generation
            or active_target(uow) != self._target
        ):
            raise SyncJobObsoleteError("Sync settings changed; start a new reviewed job.")

    def _check_job(self, cancelled: Callable[[], bool], deadline: datetime) -> None:
        if cancelled():
            raise SyncJobCancelledError("Sync cancelled; unverified changes remain pending.")
        if utc_instant(self._clock.now()) >= deadline:
            raise TimeoutError("Sync deadline expired; unverified changes remain pending.")
        with self._factory() as uow:
            self._ensure_binding(uow)

    def _read(self) -> tuple[SyncChange, ...]:
        try:
            return self._gateway.read_changes()
        except ValueError:
            with self._factory() as uow:
                self._ensure_binding(uow)
                for problem in self._gateway.read_problems():
                    uow.sync_for(self._target).record_problem(problem)
            raise

    def _stage(self, changes: Sequence[SyncChange]) -> bool:
        with self._factory() as uow:
            self._ensure_binding(uow)
            repo = uow.sync_for(self._target)
            repo.observe(changes)
            for problem in self._gateway.read_problems():
                repo.record_problem(problem)
            groups: dict[str, list[SyncChange]] = {}
            for change in repo.observed():
                groups.setdefault(change.group_id, []).append(change)
            incomplete = []
            for group_id, group in groups.items():
                first = group[0]
                actual = {change.change_id for change in group}
                if actual < set(first.group_members) and all(
                    change.group_members == first.group_members
                    and change.group_digest == first.group_digest
                    and change.aggregate_base_heads == first.aggregate_base_heads
                    for change in group
                ):
                    incomplete.append(group_id)
                    continue
                try:
                    validate_group(group)
                except ValueError:
                    raw = [change.to_record() for change in group]
                    identifier = hashlib.sha256(canonical_json(raw).encode("utf-8")).hexdigest()
                    repo.record_problem(
                        SyncProblem(identifier, "invalid_group", raw, "group:" + group_id)
                    )
            repo.set_state("incomplete_groups", incomplete)
            return bool(repo.problems()) or bool(incomplete)

    @staticmethod
    def _groups(changes: Sequence[SyncChange]) -> tuple[tuple[SyncChange, ...], ...]:
        groups: dict[str, list[SyncChange]] = {}
        for change in changes:
            groups.setdefault(change.group_id, []).append(change)
        result = tuple(tuple(group) for group in groups.values())
        for group in result:
            validate_group(group)
        return result

    def publish_once(self, *, cancelled: Callable[[], bool], deadline: datetime) -> int:
        """Verify exact IDs; never equate a failed response with a failed append."""
        deadline = utc_instant(deadline)
        self._check_job(cancelled, deadline)
        remote = self._read()
        if self._stage(remote):
            raise SyncContentError("Unresolved sync data is staged; publication is paused.")
        self._check_job(cancelled, deadline)
        remote_by_id = {change.change_id: change for change in remote}
        outgoing: list[SyncChange] = []
        already_present: list[str] = []
        with self._factory() as uow:
            self._ensure_binding(uow)
            repo = uow.sync_for(self._target)
            if repo.conflicts():
                raise SyncContentError("Resolve sync conflicts before publishing changes.")
            for group in self._groups(repo.pending()):
                if all(remote_by_id.get(change.change_id) == change for change in group):
                    already_present.extend(change.change_id for change in group)
                elif all(
                    not_before is None or not_before <= self._clock.now()
                    for not_before in (
                        repo.publication(change.change_id).not_before for change in group
                    )
                ):
                    outgoing.extend(group)
            repo.acknowledge(already_present)
            repo.mark_attempted(tuple(change.change_id for change in outgoing))
        if not outgoing:
            return len(already_present)
        self._check_job(cancelled, deadline)
        self._gateway.append_changes(outgoing)
        # Readback uses this job's immutable target, including if settings changed meanwhile.
        verified = self._read()
        self._check_job(cancelled, deadline)
        if self._stage(verified):
            raise SyncContentError("Readback contains quarantined data; changes remain pending.")
        verified_by_id = {change.change_id: change for change in verified}
        if not all(verified_by_id.get(change.change_id) == change for change in outgoing):
            raise SyncContentError("Append could not be verified; retry with the original IDs.")
        with self._factory() as uow:
            self._ensure_binding(uow)
            uow.sync_for(self._target).acknowledge(tuple(change.change_id for change in outgoing))
        return len(already_present) + len(outgoing)


@dataclass(frozen=True)
class SyncResult:
    pending_count: int
    conflict_count: int
    last_success: datetime | None
    state: str


class SyncService(SyncPublicationService):
    """Reconcile every pull before publishing or confirming a successful job."""

    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        gateway: SyncGateway,
        target: SyncTarget,
        clock: Clock,
        identifiers: IdentifierGenerator,
        *,
        generation: int,
    ) -> None:
        super().__init__(uow_factory, gateway, target, clock, generation=generation)
        self._reconciler = SyncReconciler(
            uow_factory, target, clock, identifiers, generation=generation
        )
        self._identifiers = identifiers
        self._reviews: dict[str, SyncConflictReview] = {}

    @staticmethod
    def _local_payloads(uow: UnitOfWork) -> dict[EntityKey, Mapping[str, object] | None]:
        records: dict[EntityKey, Mapping[str, object] | None] = {
            ("work_session", str(s.id)): None if s.deleted_at else session_payload(s)
            for s in uow.sessions.list_all()
        }
        records.update(
            {
                ("deduction", str(d.id)): None if d.deleted_at else deduction_payload(d)
                for d in uow.deductions.list_all()
            }
        )
        records.update(
            {("day_details", d.work_date.isoformat()): day_payload(d) for d in uow.days.list_all()}
        )
        return records

    @classmethod
    def _local_fingerprint(cls, uow: UnitOfWork) -> str:
        records = [
            {"kind": key[0], "id": key[1], "payload": value}
            for key, value in sorted(cls._local_payloads(uow).items())
        ]
        return hashlib.sha256(canonical_json(records).encode("utf-8")).hexdigest()

    def review(self, conflict_id: str) -> SyncConflictReview:
        self._reconciler.reconcile()
        with self._factory() as uow:
            self._ensure_binding(uow)
            repo = uow.sync_for(self._target)
            conflict = next((c for c in repo.conflicts() if c.conflict_id == conflict_id), None)
            if conflict is None:
                raise SyncReviewError("Conflict changed; open the current review.")
            local = self._local_payloads(uow)
            ancestors = {parent for change in conflict.changes for parent in change.parent_ids}
            ancestors.update(
                head
                for change in conflict.changes
                for heads in change.aggregate_base_heads.values()
                for head in heads
            )
            review = SyncConflictReview(
                conflict,
                {key: local.get(key) for key in conflict.entity_keys},
                {key: repo.heads(key) for key in conflict.entity_keys},
                self._local_fingerprint(uow),
                tuple(c for c in repo.observed() if c.change_id in ancestors),
            )
        self._reviews[conflict_id] = review
        return review

    def resolve(
        self,
        conflict_id: str,
        reviewed_head_ids: frozenset[str],
        chosen_payloads: Mapping[EntityKey, Mapping[str, object] | None],
    ) -> None:
        """A resolution is one atomic command descending from every reviewed version."""
        # Newly staged observations can invalidate an open dialog. Unseen remote writes
        # remain independent and become a new conflict on the next pull.
        self._reconciler.reconcile()
        review = self._reviews.get(conflict_id)
        if review is None or review.conflict.head_ids != reviewed_head_ids:
            raise SyncReviewError("Open a fresh conflict review before choosing a resolution.")
        with self._factory() as uow:
            self._ensure_binding(uow)
            repo = uow.sync_for(self._target)
            current = next((c for c in repo.conflicts() if c.conflict_id == conflict_id), None)
            if current is None or current.head_ids != reviewed_head_ids:
                raise SyncReviewError("Conflict heads changed; review every current version.")
            if set(chosen_payloads) != set(current.entity_keys):
                raise SyncReviewError("Choose a payload or deletion for every affected entry.")
            if self._local_fingerprint(uow) != review.local_fingerprint or any(
                repo.heads(key) != heads for key, heads in review.local_heads.items()
            ):
                raise SyncReviewError("The local timesheet changed; open a fresh review.")
            if repo.problems():
                raise SyncReviewError("Quarantined sync data must be reviewed before resolution.")
            now = self._clock.now()
            sessions = {str(s.id): replace(s) for s in uow.sessions.list_all()}
            deductions = {str(d.id): replace(d) for d in uow.deductions.list_all()}
            days = {d.work_date.isoformat(): d for d in uow.days.list_all()}
            bases = {
                key: tuple(
                    sorted(
                        set(repo.heads(key))
                        | {c.change_id for c in current.changes if c.entity_key == key}
                    )
                )
                for key in current.entity_keys
            }
            device_id = uow.settings.get("sync_device_id")
            if not isinstance(device_id, str) or not device_id:
                device_id = self._identifiers.change_id()
                uow.settings.save("sync_device_id", device_id, now)
            group_id = self._identifiers.group_id()
            drafts = [
                SyncChange(
                    self._identifiers.change_id(),
                    2,
                    key[0],
                    key[1],
                    bases[key],
                    group_id,
                    (),
                    "",
                    bases,
                    "upsert" if payload is not None else "delete",
                    payload,
                    now,
                    device_id,
                )
                for key, payload in sorted(chosen_payloads.items())
            ]
            changes = finalize_group(drafts)
            for change in changes:
                self._reconciler._apply_candidate(change, sessions, deductions, days, now)
            validate_completed_parents(changes, sessions, deductions)
            for deduction in deductions.values():
                if (
                    deduction.deleted_at is None
                    and (parent := sessions.get(str(deduction.session_id))) is not None
                    and parent.deleted_at is not None
                ):
                    raise SyncReviewError("Delete the affected deductions with their work session.")
            validate_intervals(tuple(sessions.values()), tuple(deductions.values()), as_of=now)
            # All validation precedes persistence. The outbox, payloads, heads and conflict
            # closure share this UoW, including rollback when an insertion/commit fails.
            repo.enqueue(changes)
            for change in sorted(changes, key=lambda c: c.entity_kind != "work_session"):
                key = change.entity_key
                if key[0] == "work_session" and key[1] in sessions:
                    if uow.sessions.get(sessions[key[1]].id) is None:
                        uow.sessions.add(sessions[key[1]])
                    else:
                        uow.sessions.save(sessions[key[1]])
                elif key[0] == "deduction" and key[1] in deductions:
                    if uow.deductions.get(deductions[key[1]].id) is None:
                        uow.deductions.add(deductions[key[1]])
                    else:
                        uow.deductions.save(deductions[key[1]])
                elif key[0] == "day_details":
                    if key[1] in days:
                        uow.days.save(days[key[1]])
                    else:
                        uow.days.delete(date.fromisoformat(key[1]))
                repo.set_heads(key, (change.change_id,))
            repo.close_conflict(conflict_id, reviewed_head_ids)
        self._reviews.pop(conflict_id, None)

    def _stage(self, changes: Sequence[SyncChange]) -> bool:
        protocol_blocked = super()._stage(changes)
        result = self._reconciler.reconcile()
        return protocol_blocked or result.conflict_count > 0 or result.staged_count > 0

    def run_once(self, *, cancelled: Callable[[], bool], deadline: datetime) -> SyncResult:
        # Reconciliation evidence stays durable; status contains no private payloads.
        with suppress(SyncContentError):
            self.publish_once(cancelled=cancelled, deadline=deadline)
        self._check_job(cancelled, utc_instant(deadline))
        with self._factory() as uow:
            self._ensure_binding(uow)
            repo = uow.sync_for(self._target)
            pending, conflicts = len(repo.pending()), len(repo.conflicts())
            staged = bool(repo.get_state("staged_reconciliation"))
            invalid = bool(repo.problems())
            state = (
                "invalid_data"
                if invalid
                else "conflict"
                if conflicts
                else "pending"
                if pending or staged
                else "synced"
            )
            saved = repo.get_state("last_success")
            last_success = datetime.fromisoformat(saved) if isinstance(saved, str) else None
            if state == "synced":
                last_success = utc_instant(self._clock.now())
                repo.set_state("last_success", last_success.isoformat())
            return SyncResult(pending, conflicts, last_success, state)


def _stamp(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None


def _read_stamp(value: object) -> datetime | None:
    return datetime.fromisoformat(value).astimezone(UTC) if isinstance(value, str) else None


def _session_record(session: WorkSession) -> dict[str, Any]:
    return {
        "kind": "work_session",
        "id": str(session.id),
        "revision": session.revision,
        "updated_at_utc": _stamp(session.updated_at) or "",
        "payload": {
            "actual_started_at": _stamp(session.actual_started_at),
            "actual_ended_at": _stamp(session.actual_ended_at),
            "effective_started_at": _stamp(session.effective_started_at),
            "effective_ended_at": _stamp(session.effective_ended_at),
            "source": session.source.value,
            "created_at": _stamp(session.created_at),
            "deleted_at": _stamp(session.deleted_at),
            "recovery_acknowledged_at": _stamp(session.recovery_acknowledged_at),
            "rounding_minutes": session.rounding_minutes,
            "testhuset_task_id": session.testhuset_task_id,
            "dsb_allocation_id": session.dsb_allocation_id,
        },
    }


def _deduction_record(deduction: Deduction) -> dict[str, Any]:
    return {
        "kind": "deduction",
        "id": str(deduction.id),
        "revision": deduction.revision,
        "updated_at_utc": _stamp(deduction.updated_at) or "",
        "payload": {
            "session_id": str(deduction.session_id),
            "kind": deduction.kind.value,
            "actual_started_at": _stamp(deduction.actual_started_at),
            "actual_ended_at": _stamp(deduction.actual_ended_at),
            "effective_started_at": _stamp(deduction.effective_started_at),
            "effective_ended_at": _stamp(deduction.effective_ended_at),
            "source": deduction.source.value,
            "created_at": _stamp(deduction.created_at),
            "deleted_at": _stamp(deduction.deleted_at),
            "rounding_minutes": deduction.rounding_minutes,
        },
    }


def _day_details_record(details: DayDetails) -> dict[str, Any]:
    return {
        "kind": "day_details",
        "id": details.work_date.isoformat(),
        "revision": details.revision,
        "updated_at_utc": "",
        "payload": {"location": details.location.value, "note": details.note},
    }


class GoogleSyncService:
    def __init__(self, uow_factory: Callable[[], UnitOfWork], gateway: GoogleSyncGateway) -> None:
        self._uow_factory, self._gateway = uow_factory, gateway

    def sync_completed_records(self) -> int:
        """Refuse the unsafe snapshot protocol before reading or mutating any state."""
        raise GoogleSyncUpgradeRequiredError(
            "Google sync requires an upgrade and a reviewed V2 migration before syncing. "
            "Local time tracking remains available; keep your existing sheet for migration."
        )

    @staticmethod
    def _local_records(uow: UnitOfWork) -> list[dict[str, Any]]:
        return [
            *[
                _session_record(session)
                for session in uow.sessions.list_all()
                if session.actual_ended_at
            ],
            *[
                _deduction_record(item)
                for item in uow.deductions.list_all()
                if item.actual_ended_at
            ],
            *[_day_details_record(details) for details in uow.days.list_all()],
        ]

    @staticmethod
    def _key(record: dict[str, Any]) -> tuple[str, str]:
        kind, identifier = record.get("kind"), record.get("id")
        if not isinstance(kind, str) or not isinstance(identifier, str):
            raise ValueError("The shared sync sheet contains an invalid record.")
        return kind, identifier

    def _merge(
        self, local: list[dict[str, Any]], remote: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        merged = {self._key(record): record for record in remote}
        for local_record in local:
            key = self._key(local_record)
            remote_record = merged.get(key)
            if remote_record is None:
                merged[key] = local_record
                continue
            local_revision, remote_revision = (
                local_record.get("revision"),
                remote_record.get("revision"),
            )
            if not isinstance(local_revision, int) or not isinstance(remote_revision, int):
                raise ValueError("The shared sync sheet contains an invalid record revision.")
            if local_revision == remote_revision and local_record != remote_record:
                remote_payload = remote_record.get("payload")
                if isinstance(remote_payload, dict) and "source" not in remote_payload:
                    # Rows written by v0.2.2 contained only the minimal payload. The
                    # complete current record is the compatible upgrade of that row.
                    merged[key] = local_record
                    continue
                raise GoogleSyncConflictError(
                    "The same record was changed on both machines. Resolve it before syncing."
                )
            if local_revision > remote_revision:
                merged[key] = local_record
        return list(merged.values())

    def _apply_remote_records(self, uow: UnitOfWork, records: list[dict[str, Any]]) -> None:
        for record in records:
            if record["kind"] == "work_session":
                incoming_session = self._session_from_record(record)
                existing_session = uow.sessions.get(incoming_session.id)
                if existing_session is None:
                    uow.sessions.add(incoming_session)
                elif incoming_session.revision > existing_session.revision:
                    uow.sessions.save(incoming_session)
        for record in records:
            if record["kind"] == "deduction":
                incoming_deduction = self._deduction_from_record(record)
                existing_deduction = uow.deductions.get(incoming_deduction.id)
                if existing_deduction is None:
                    uow.deductions.add(incoming_deduction)
                elif incoming_deduction.revision > existing_deduction.revision:
                    uow.deductions.save(incoming_deduction)
        for record in records:
            if record["kind"] == "day_details":
                incoming_day = self._day_details_from_record(record)
                existing_day = uow.days.get(incoming_day.work_date)
                if existing_day is None or incoming_day.revision > existing_day.revision:
                    uow.days.save(incoming_day)

    @staticmethod
    def _payload(record: dict[str, Any]) -> dict[str, Any]:
        payload = record.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("The shared sync sheet contains an invalid record payload.")
        return payload

    def _session_from_record(self, record: dict[str, Any]) -> WorkSession:
        payload = self._payload(record)
        started, ended = (
            _read_stamp(payload.get("actual_started_at")),
            _read_stamp(payload.get("actual_ended_at")),
        )
        if started is None or ended is None:
            raise ValueError("The shared sync sheet contains an incomplete work session.")
        return WorkSession(
            id=SessionId(record["id"]),
            actual_started_at=started,
            actual_ended_at=ended,
            effective_started_at=_read_stamp(payload.get("effective_started_at")),
            effective_ended_at=_read_stamp(payload.get("effective_ended_at")),
            source=EntrySource(payload.get("source", EntrySource.MANUAL.value)),
            created_at=_read_stamp(payload.get("created_at")) or ended,
            updated_at=_read_stamp(record.get("updated_at_utc")) or ended,
            deleted_at=_read_stamp(payload.get("deleted_at")),
            recovery_acknowledged_at=_read_stamp(payload.get("recovery_acknowledged_at")),
            rounding_minutes=payload.get("rounding_minutes", 1),
            revision=record["revision"],
            testhuset_task_id=payload.get("testhuset_task_id"),
            dsb_allocation_id=payload.get("dsb_allocation_id"),
        )

    def _deduction_from_record(self, record: dict[str, Any]) -> Deduction:
        payload = self._payload(record)
        started, ended = (
            _read_stamp(payload.get("actual_started_at")),
            _read_stamp(payload.get("actual_ended_at")),
        )
        session_id, kind = payload.get("session_id"), payload.get("kind")
        if (
            started is None
            or ended is None
            or not isinstance(session_id, str)
            or not isinstance(kind, str)
        ):
            raise ValueError("The shared sync sheet contains an incomplete deduction.")
        return Deduction(
            id=DeductionId(record["id"]),
            session_id=SessionId(session_id),
            kind=DeductionKind(kind),
            actual_started_at=started,
            actual_ended_at=ended,
            effective_started_at=_read_stamp(payload.get("effective_started_at")),
            effective_ended_at=_read_stamp(payload.get("effective_ended_at")),
            source=EntrySource(payload.get("source", EntrySource.MANUAL.value)),
            created_at=_read_stamp(payload.get("created_at")) or ended,
            updated_at=_read_stamp(record.get("updated_at_utc")) or ended,
            deleted_at=_read_stamp(payload.get("deleted_at")),
            rounding_minutes=payload.get("rounding_minutes", 1),
            revision=record["revision"],
        )

    def _day_details_from_record(self, record: dict[str, Any]) -> DayDetails:
        payload = self._payload(record)
        location, note = payload.get("location"), payload.get("note")
        if not isinstance(location, str) or not isinstance(note, str):
            raise ValueError("The shared sync sheet contains invalid day details.")
        try:
            return DayDetails(
                date.fromisoformat(record["id"]), WorkLocation(location), note, record["revision"]
            )
        except (TypeError, ValueError) as error:
            raise ValueError("The shared sync sheet contains invalid day details.") from error
