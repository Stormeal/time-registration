"""Cross-machine Google sync behavior without Qt, SQLite, or Google APIs."""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import UTC, date, datetime
from typing import Any, cast

import pytest

from qi_flow.application.google_sync_service import GoogleSyncService
from qi_flow.application.ports import UnitOfWork as UnitOfWorkPort
from qi_flow.domain.models import (
    DayDetails,
    Deduction,
    EntrySource,
    SessionId,
    WorkLocation,
    WorkSession,
)


class Sessions:
    def __init__(self) -> None:
        self.items: list[WorkSession] = []

    def list_all(self) -> list[WorkSession]:
        return self.items

    def get(self, session_id: SessionId) -> WorkSession | None:
        return next((item for item in self.items if item.id == session_id), None)

    def add(self, session: WorkSession) -> None:
        self.items.append(session)

    def save(self, session: WorkSession) -> None:
        self.items[self.items.index(self.get(session.id))] = session  # type: ignore[arg-type]


class Deductions:
    def __init__(self) -> None:
        self.items: list[Deduction] = []

    def list_all(self) -> list[Deduction]:
        return self.items

    def get(self, deduction_id: object) -> Deduction | None:
        return next((item for item in self.items if item.id == deduction_id), None)

    def add(self, deduction: Deduction) -> None:
        self.items.append(deduction)

    def save(self, deduction: Deduction) -> None:
        self.items[self.items.index(self.get(deduction.id))] = deduction  # type: ignore[arg-type]


class Days:
    def __init__(self) -> None:
        self.items: list[DayDetails] = []

    def list_all(self) -> list[DayDetails]:
        return self.items

    def get(self, work_date: date) -> DayDetails | None:
        return next((item for item in self.items if item.work_date == work_date), None)

    def save(self, details: DayDetails) -> None:
        existing = self.get(details.work_date)
        if existing is None:
            self.items.append(details)
        else:
            self.items[self.items.index(existing)] = details


class UnitOfWork(AbstractContextManager[object]):
    def __init__(self) -> None:
        self.sessions = Sessions()
        self.deductions = Deductions()
        self.days = Days()

    def __enter__(self) -> UnitOfWork:
        return self

    def __exit__(self, *args: object) -> None:
        return None


class Gateway:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = records
        self.read_calls = 0
        self.write_calls = 0

    def read_records(self) -> list[dict[str, Any]]:
        self.read_calls += 1
        return self.records

    def replace_records(self, records: list[dict[str, Any]]) -> int:
        self.write_calls += 1
        self.records = records
        return len(records)


def test_legacy_mapper_remains_available_for_explicit_migration() -> None:
    remote = [
        {
            "kind": "work_session",
            "id": "shared-session",
            "revision": 1,
            "updated_at_utc": "2026-09-17T10:39:34+00:00",
            "payload": {
                "actual_started_at": "2026-09-17T05:00:00+00:00",
                "actual_ended_at": "2026-09-17T14:15:00+00:00",
                "testhuset_task_id": None,
                "dsb_allocation_id": None,
            },
        }
    ]
    unit_of_work = UnitOfWork()
    gateway = Gateway(remote)
    service = GoogleSyncService(lambda: cast(UnitOfWorkPort, unit_of_work), gateway)

    mapped = service._session_from_record(remote[0])

    assert mapped.id == SessionId("shared-session")
    assert mapped.source == EntrySource.MANUAL
    assert unit_of_work.sessions.items == []
    assert gateway.records[0]["id"] == "shared-session"
    assert gateway.read_calls == gateway.write_calls == 0


def test_legacy_day_serializer_remains_available_for_reviewed_migration() -> None:
    unit_of_work = UnitOfWork()
    unit_of_work.days.save(DayDetails(date(2026, 9, 17), WorkLocation.OFFICE, "Workshop", 2))
    gateway = Gateway([])
    service = GoogleSyncService(lambda: cast(UnitOfWorkPort, unit_of_work), gateway)

    records = service._local_records(cast(UnitOfWorkPort, unit_of_work))
    assert records[0]["kind"] == "day_details"
    assert records[0]["payload"] == {"location": "office", "note": "Workshop"}


def test_legacy_session_serializer_remains_available_for_reviewed_migration() -> None:
    unit_of_work = UnitOfWork()
    unit_of_work.sessions.add(
        WorkSession(
            SessionId("manual-session"),
            datetime(2026, 9, 17, 8, tzinfo=UTC),
            datetime(2026, 9, 17, 16, tzinfo=UTC),
            source=EntrySource.MANUAL,
            created_at=datetime(2026, 9, 17, 16, tzinfo=UTC),
            updated_at=datetime(2026, 9, 17, 16, tzinfo=UTC),
        )
    )
    gateway = Gateway([])

    service = GoogleSyncService(lambda: cast(UnitOfWorkPort, unit_of_work), gateway)
    records = service._local_records(cast(UnitOfWorkPort, unit_of_work))

    assert records[0]["payload"]["source"] == "manual"


def test_public_v1_sync_requires_upgrade_before_any_gateway_or_local_access() -> None:
    unit_of_work = UnitOfWork()
    unit_of_work.days.save(DayDetails(date(2026, 9, 17), WorkLocation.OFFICE, "Keep local", 2))
    gateway = Gateway(
        [
            {
                "kind": "day_details",
                "id": "2026-09-17",
                "revision": 3,
                "payload": {"location": "remote", "note": "Keep remote"},
            }
        ]
    )
    opened = 0

    def open_uow() -> UnitOfWorkPort:
        nonlocal opened
        opened += 1
        return cast(UnitOfWorkPort, unit_of_work)

    service = GoogleSyncService(open_uow, gateway)
    with pytest.raises(ValueError, match=r"upgrade|Upgrade"):
        service.sync_completed_records()
    assert opened == gateway.read_calls == gateway.write_calls == 0
    assert unit_of_work.days.items[0].note == "Keep local"
    assert gateway.records[0]["payload"]["note"] == "Keep remote"
