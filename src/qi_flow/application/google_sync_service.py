"""Conflict-safe synchronization of completed records through a gateway."""

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any, Protocol

from qi_flow.application.ports import UnitOfWork
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
        """Merge remote and local records before replacing the remote collection.

        A fresh machine imports shared history before it writes anything, so an empty
        local database cannot erase the sheet.
        """
        remote_records = self._gateway.read_records()
        with self._uow_factory() as uow:
            local_records = self._local_records(uow)
            merged = self._merge(local_records, remote_records)
            self._apply_remote_records(uow, merged)
            # Re-serialize imported legacy rows so the next machine receives the
            # complete current payload instead of the old minimal format.
            merged = self._merge(self._local_records(uow), merged)
        return self._gateway.replace_records(merged)

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
