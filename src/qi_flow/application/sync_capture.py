"""Capture command changes in their existing local transaction, without networking."""

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime

from qi_flow.application.ports import IdentifierGenerator, UnitOfWork
from qi_flow.application.sync_models import (
    EntityKey,
    Operation,
    SyncChange,
    SyncTarget,
    finalize_group,
)
from qi_flow.application.sync_payloads import day_payload, deduction_payload, session_payload


def active_target(uow: UnitOfWork) -> SyncTarget | None:
    """Only a reviewed destination may capture ordinary local changes."""
    if uow.settings.get("google_sync_enabled") is not True:
        return None
    binding = uow.settings.get("google_sync_v2_target")
    if not isinstance(binding, dict):
        return None
    spreadsheet_id, log_id = binding.get("spreadsheet_id"), binding.get("log_id")
    if not isinstance(spreadsheet_id, str) or not isinstance(log_id, str):
        return None
    target = SyncTarget(spreadsheet_id, log_id)
    return target if uow.sync_for(target).get_state("migration_complete") is True else None


@dataclass(frozen=True)
class _Record:
    root: EntityKey | None
    operation: Operation
    payload: Mapping[str, object] | None


def _records(uow: UnitOfWork) -> dict[EntityKey, _Record]:
    sessions = {
        session.id: session
        for session in uow.sessions.list_all()
        if session.actual_ended_at is not None
    }
    records: dict[EntityKey, _Record] = {}
    for session in sessions.values():
        key = ("work_session", str(session.id))
        records[key] = _Record(
            key,
            "delete" if session.deleted_at else "upsert",
            None if session.deleted_at else session_payload(session),
        )
    for deduction in uow.deductions.list_all():
        parent = sessions.get(deduction.session_id)
        if parent is None:
            continue
        deleted = deduction.deleted_at is not None or parent.deleted_at is not None
        if deduction.actual_ended_at is None and not deleted:
            continue
        records[("deduction", str(deduction.id))] = _Record(
            ("work_session", str(parent.id)),
            "delete" if deleted else "upsert",
            None if deleted else deduction_payload(deduction),
        )
    for details in uow.days.list_all():
        records[("day_details", details.work_date.isoformat())] = _Record(
            None, "upsert", day_payload(details)
        )
    return records


@contextmanager
def captured_mutation(
    factory: Callable[[], UnitOfWork],
    identifiers: IdentifierGenerator,
    now: datetime,
    *,
    not_before: datetime | None = None,
) -> Iterator[UnitOfWork]:
    """Both record changes and outbox/head changes commit or roll back together."""
    with factory() as uow:
        target = active_target(uow)
        before = _records(uow) if target is not None else {}
        yield uow
        if target is None:
            return
        after = _records(uow)
        changed = {key for key in before.keys() | after.keys() if before.get(key) != after.get(key)}
        if not changed:
            return
        roots = {
            record.root
            for key in changed
            for record in (before.get(key), after.get(key))
            if record is not None and record.root is not None
        }
        keys = changed | {
            key
            for snapshot in (before, after)
            for key, record in snapshot.items()
            if record.root in roots and record.root is not None
        }
        repo = uow.sync_for(target)
        bases = {key: repo.heads(key) for key in sorted(keys)}
        # Descendants of a still deferred finish cannot bypass its publication grace.
        deadline = not_before
        pending = {change.change_id for change in repo.pending()}
        for heads in bases.values():
            for head in heads:
                if head in pending:
                    ancestor_deadline = repo.publication(head).not_before
                    if ancestor_deadline is not None and (
                        deadline is None or ancestor_deadline > deadline
                    ):
                        deadline = ancestor_deadline
        device_id = uow.settings.get("sync_device_id")
        if not isinstance(device_id, str) or not device_id:
            device_id = identifiers.change_id()
            uow.settings.save("sync_device_id", device_id, now)
        group_id = identifiers.group_id()
        drafts = []
        for key in sorted(keys):
            record = after.get(key)
            drafts.append(
                SyncChange(
                    change_id=identifiers.change_id(),
                    schema_version=2,
                    entity_kind=key[0],
                    entity_id=key[1],
                    parent_ids=bases[key],
                    group_id=group_id,
                    group_members=(),
                    group_digest="",
                    aggregate_base_heads=bases,
                    operation=record.operation if record is not None else "withdraw",
                    payload=record.payload if record is not None else None,
                    created_at=now,
                    device_id=device_id,
                )
            )
        changes = finalize_group(drafts)
        repo.enqueue(changes)
        if deadline is not None:
            repo.defer(tuple(change.change_id for change in changes), deadline)
        for change in changes:
            repo.set_heads(change.entity_key, (change.change_id,))
