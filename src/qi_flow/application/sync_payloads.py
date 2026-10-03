"""Structured entity payloads at the sync boundary; no operational settings."""

from datetime import UTC, datetime

from qi_flow.domain.models import DayDetails, Deduction, WorkSession


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
