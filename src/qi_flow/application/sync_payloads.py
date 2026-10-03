"""Structured entity payloads at the sync boundary; no operational settings."""

from collections.abc import Mapping
from datetime import UTC, date, datetime

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


def stamp(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None


def session_payload(session: WorkSession) -> dict[str, object]:
    return {
        "actual_started_at": stamp(session.actual_started_at),
        "actual_ended_at": stamp(session.actual_ended_at),
        "effective_started_at": stamp(session.effective_started_at),
        "effective_ended_at": stamp(session.effective_ended_at),
        "source": session.source.value,
        "created_at": stamp(session.created_at),
        "updated_at": stamp(session.updated_at),
        "deleted_at": stamp(session.deleted_at),
        "rounding_minutes": session.rounding_minutes,
        "revision": session.revision,
        "testhuset_task_id": session.testhuset_task_id,
        "dsb_allocation_id": session.dsb_allocation_id,
    }


def deduction_payload(deduction: Deduction) -> dict[str, object]:
    return {
        "session_id": str(deduction.session_id),
        "kind": deduction.kind.value,
        "actual_started_at": stamp(deduction.actual_started_at),
        "actual_ended_at": stamp(deduction.actual_ended_at),
        "effective_started_at": stamp(deduction.effective_started_at),
        "effective_ended_at": stamp(deduction.effective_ended_at),
        "source": deduction.source.value,
        "created_at": stamp(deduction.created_at),
        "updated_at": stamp(deduction.updated_at),
        "deleted_at": stamp(deduction.deleted_at),
        "rounding_minutes": deduction.rounding_minutes,
        "revision": deduction.revision,
    }


def day_payload(details: DayDetails) -> dict[str, object]:
    return {"location": details.location.value, "note": details.note, "revision": details.revision}


def _text(payload: Mapping[str, object], field: str) -> str:
    value = payload[field]
    if not isinstance(value, str):
        raise ValueError("Invalid V2 text field.")
    return value


def _optional_text(payload: Mapping[str, object], field: str) -> str | None:
    if payload[field] is None:
        return None
    value = _text(payload, field)
    if not value.strip():
        raise ValueError("Invalid V2 identifier.")
    return value


def _integer(payload: Mapping[str, object], field: str) -> int:
    value = payload[field]
    if type(value) is not int or value <= 0:
        raise ValueError("Invalid V2 integer field.")
    return value


def _instant(
    payload: Mapping[str, object], field: str, *, required: bool = False
) -> datetime | None:
    value = payload[field]
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise ValueError("V2 requires a completed timezone-aware interval.")
    instant = datetime.fromisoformat(value)
    if instant.utcoffset() is None:
        raise ValueError("V2 timestamps must be timezone-aware.")
    return instant.astimezone(UTC)


def read_payload(
    kind: str, entity_id: str, payload: Mapping[str, object]
) -> WorkSession | Deduction | DayDetails:
    """Only the explicit V2 schema is accepted, including all nullable fields."""
    common = {
        "actual_started_at",
        "actual_ended_at",
        "effective_started_at",
        "effective_ended_at",
        "source",
        "created_at",
        "updated_at",
        "deleted_at",
        "rounding_minutes",
        "revision",
    }
    fields = {
        "work_session": common | {"testhuset_task_id", "dsb_allocation_id"},
        "deduction": common | {"session_id", "kind"},
        "day_details": {"location", "note", "revision"},
    }
    if kind not in fields or set(payload) != fields[kind]:
        raise ValueError("Unsupported V2 payload fields or entity kind.")
    revision = _integer(payload, "revision")
    if kind == "day_details":
        work_date = date.fromisoformat(entity_id)
        if work_date.isoformat() != entity_id:
            raise ValueError("V2 day IDs must be canonical ISO dates.")
        return DayDetails(
            work_date, WorkLocation(_text(payload, "location")), _text(payload, "note"), revision
        )
    if payload["deleted_at"] is not None:
        raise ValueError("V2 deletion requires a tombstone operation.")
    start = _instant(payload, "actual_started_at", required=True)
    end = _instant(payload, "actual_ended_at", required=True)
    assert start is not None and end is not None
    if kind == "work_session":
        return WorkSession(
            id=SessionId(entity_id),
            actual_started_at=start,
            actual_ended_at=end,
            effective_started_at=_instant(payload, "effective_started_at"),
            effective_ended_at=_instant(payload, "effective_ended_at"),
            source=EntrySource(_text(payload, "source")),
            created_at=_instant(payload, "created_at", required=True),
            updated_at=_instant(payload, "updated_at", required=True),
            rounding_minutes=_integer(payload, "rounding_minutes"),
            revision=revision,
            testhuset_task_id=_optional_text(payload, "testhuset_task_id"),
            dsb_allocation_id=_optional_text(payload, "dsb_allocation_id"),
        )
    return Deduction(
        id=DeductionId(entity_id),
        session_id=SessionId(_text(payload, "session_id")),
        kind=DeductionKind(_text(payload, "kind")),
        actual_started_at=start,
        actual_ended_at=end,
        effective_started_at=_instant(payload, "effective_started_at"),
        effective_ended_at=_instant(payload, "effective_ended_at"),
        source=EntrySource(_text(payload, "source")),
        created_at=_instant(payload, "created_at", required=True),
        updated_at=_instant(payload, "updated_at", required=True),
        rounding_minutes=_integer(payload, "rounding_minutes"),
        revision=revision,
    )
