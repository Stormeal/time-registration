"""Explicit all-participant cutover from frozen V1 snapshots to immutable V2 seeds."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, cast

from qi_flow.application.google_sync import GoogleSyncConfiguration
from qi_flow.application.ports import Clock, IdentifierGenerator, SyncGateway, UnitOfWork
from qi_flow.application.sync_models import (
    Operation,
    SyncChange,
    SyncJobCancelledError,
    SyncJobObsoleteError,
    SyncProblem,
    SyncTarget,
    canonical_json,
    finalize_group,
    freeze_json,
    parse_json,
    utc_instant,
)
from qi_flow.application.sync_payloads import (
    day_payload,
    deduction_payload,
    read_payload,
    session_payload,
)
from qi_flow.application.sync_reconciliation import SyncReconciler
from qi_flow.domain.errors import DomainError

type Record = Mapping[str, object]


def fingerprint(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _mapping(value: object) -> Record:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ValueError("Invalid migration record.")
    return value


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Migration identifiers must be nonempty.")
    return value


@dataclass(frozen=True)
class LocalSnapshot:
    """An adapter-verified safety copy plus the completed records read from that copy."""

    backup_reference: str
    records: tuple[Record, ...]

    def __post_init__(self) -> None:
        _text(self.backup_reference)
        object.__setattr__(self, "records", tuple(_mapping(freeze_json(r)) for r in self.records))


class MigrationSafety(Protocol):
    def create_snapshot(self) -> LocalSnapshot: ...


class MigrationGateway(SyncGateway, Protocol):
    def read_legacy_rows(self) -> Sequence[Sequence[object]]: ...

    def read_migration_events(self) -> tuple[Record, ...]: ...

    def initialize_migration(self, target: SyncTarget, events: Sequence[Record]) -> None:
        """Atomically create both dedicated tabs; never replace an existing tab."""
        ...

    def append_migration_events(self, events: Sequence[Record]) -> None: ...


@dataclass(frozen=True)
class MigrationPlan:
    target: SyncTarget
    participants: tuple[str, ...]
    legacy_fingerprint: str
    legacy_rows: tuple[tuple[object, ...], ...]
    created_at: datetime


@dataclass(frozen=True)
class MigrationStatus:
    plan: MigrationPlan
    acknowledged: tuple[str, ...]
    completed: bool


def local_snapshot(uow: UnitOfWork) -> tuple[Record, ...]:
    """Active aggregates are preserved in the safety database, never seeded remotely."""
    records: list[Record] = []
    parents = {s.id: s for s in uow.sessions.list_all() if s.actual_ended_at is not None}
    for session in parents.values():
        records.append(
            _record("work_session", str(session.id), str(session.id), session_payload(session))
        )
    for deduction in uow.deductions.list_all():
        parent = parents.get(deduction.session_id)
        if parent is None or (
            deduction.actual_ended_at is None
            and deduction.deleted_at is None
            and parent.deleted_at is None
        ):
            continue
        record = _record(
            "deduction", str(deduction.id), str(parent.id), deduction_payload(deduction)
        )
        if parent.deleted_at is not None:
            record = {**record, "operation": "delete", "payload": None}
        records.append(record)
    records.extend(
        _record("day_details", d.work_date.isoformat(), None, day_payload(d))
        for d in uow.days.list_all()
    )
    return tuple(sorted(records, key=canonical_json))


def _record(kind: str, identifier: str, root: str | None, payload: Record) -> Record:
    deleted = payload.get("deleted_at") is not None
    return {
        "entity_kind": kind,
        "entity_id": identifier,
        "root": root,
        "operation": "delete" if deleted else "upsert",
        "payload": None if deleted else payload,
    }


def _legacy_records(rows: Sequence[Sequence[object]]) -> tuple[Record, ...]:
    """Convert only explicitly recognized V1 formats; missing fields are not version detection."""
    if not rows:
        return ()
    if list(rows[0]) != ["kind", "id", "revision", "updated_at_utc", "payload_json"]:
        raise ValueError("Unsupported legacy V1 header; preserve and review its safety copy.")
    common = {
        "actual_started_at",
        "actual_ended_at",
        "effective_started_at",
        "effective_ended_at",
        "source",
        "created_at",
        "deleted_at",
        "rounding_minutes",
    }
    result = []
    for row in rows[1:]:
        if len(row) != 5 or not isinstance(row[4], str):
            raise ValueError("Incomplete legacy V1 row.")
        kind, identifier = _text(row[0]), _text(row[1])
        payload = dict(_mapping(parse_json(row[4])))
        revision = row[2]
        if isinstance(revision, str) and revision.isdecimal():
            revision = int(revision)
        if type(revision) is not int or revision <= 0:
            raise ValueError("Invalid legacy revision.")
        if kind == "work_session":
            minimal = {
                "actual_started_at",
                "actual_ended_at",
                "testhuset_task_id",
                "dsb_allocation_id",
            }
            full = common | {"testhuset_task_id", "dsb_allocation_id", "recovery_acknowledged_at"}
            if set(payload) == minimal:
                payload.update(
                    effective_started_at=None,
                    effective_ended_at=None,
                    source="manual",
                    created_at=payload["actual_ended_at"],
                    deleted_at=None,
                    rounding_minutes=1,
                )
            elif set(payload) == full:
                payload.pop("recovery_acknowledged_at")
            else:
                raise ValueError("Unsupported legacy work payload.")
            root = identifier
        elif kind == "deduction" and set(payload) == common | {"session_id", "kind"}:
            root = _text(payload["session_id"])
        elif kind == "day_details" and set(payload) == {"location", "note"}:
            root = None
        else:
            raise ValueError("Unsupported legacy payload.")
        payload["revision"] = revision
        if kind != "day_details":
            payload["updated_at"] = row[3] or payload["actual_ended_at"]
            payload["created_at"] = payload["created_at"] or payload["actual_ended_at"]
            # Tombstones still need a recognized and valid original record, before nulling it.
            validate = {**payload, "deleted_at": None}
            read_payload(kind, identifier, validate)
        else:
            read_payload(kind, identifier, payload)
        result.append(_record(kind, identifier, root, payload))
    return tuple(sorted(result, key=canonical_json))


def seed_groups(records: Sequence[Record], plan: MigrationPlan) -> tuple[SyncChange, ...]:
    aggregates: dict[tuple[str, str], dict[tuple[str, str], Record]] = {}
    for record in records:
        if set(record) != {"entity_kind", "entity_id", "root", "operation", "payload"}:
            raise ValueError("Unknown snapshot record fields.")
        kind, identifier = _text(record["entity_kind"]), _text(record["entity_id"])
        root = record["root"]
        if kind not in {"work_session", "deduction", "day_details"} or (
            kind != "day_details" and not isinstance(root, str)
        ):
            raise ValueError("Unknown snapshot aggregate.")
        operation = record["operation"]
        if operation not in {"delete", "upsert"}:
            raise ValueError("Unsupported migration operation.")
        payload = record["payload"]
        if operation == "upsert":
            payload = dict(_mapping(payload))
            read_payload(kind, identifier, payload)
            payload["revision"] = 1
            if kind != "day_details":
                payload["created_at"] = plan.created_at.isoformat()
                payload["updated_at"] = plan.created_at.isoformat()
        elif payload is not None:
            raise ValueError("Migration tombstones must have null payloads.")
        normalized = {**record, "payload": payload}
        aggregate_key = (
            ("work_session", cast(str, root)) if root is not None else (kind, identifier)
        )
        aggregate = aggregates.setdefault(aggregate_key, {})
        key = kind, identifier
        if key in aggregate and aggregate[key] != normalized:
            raise ValueError("A snapshot contains differing duplicate entities.")
        aggregate[key] = normalized
    changes: list[SyncChange] = []
    for aggregate in aggregates.values():
        normalized_records = tuple(aggregate[key] for key in sorted(aggregate))
        group_id = "seed-group-" + fingerprint(
            {"log_id": plan.target.log_id, "records": normalized_records}
        )
        drafts = [
            SyncChange(
                "seed-"
                + fingerprint(
                    {"group_id": group_id, "kind": r["entity_kind"], "id": r["entity_id"]}
                ),
                2,
                _text(r["entity_kind"]),
                _text(r["entity_id"]),
                (),
                group_id,
                (),
                "",
                {},
                cast(Operation, r["operation"]),
                cast(Record | None, r["payload"]),
                plan.created_at,
                "migration-" + plan.target.log_id,
            )
            for r in normalized_records
        ]
        changes.extend(finalize_group(drafts))
    return tuple(sorted(changes, key=lambda c: c.change_id))


class SyncMigration:
    def __init__(
        self,
        factory: Callable[[], UnitOfWork],
        gateway: MigrationGateway,
        safety: MigrationSafety,
        clock: Clock,
        identifiers: IdentifierGenerator,
        *,
        generation: int,
        cancelled: Callable[[], bool] = lambda: False,
        deadline: datetime | None = None,
    ) -> None:
        self._factory, self._gateway, self._safety = factory, gateway, safety
        self._clock, self._identifiers, self._generation = clock, identifiers, generation
        self._cancelled, self._deadline = cancelled, deadline

    def _check(self, uow: UnitOfWork) -> None:
        if self._cancelled():
            raise SyncJobCancelledError(
                "Migration cancelled; safety copies and verified work are retained."
            )
        if self._deadline is not None and self._clock.now() >= self._deadline:
            raise TimeoutError("Migration timed out. Reopen review to verify and resume.")
        if uow.settings.get("google_sync_generation") != self._generation:
            raise SyncJobObsoleteError("Sync settings changed; reopen reviewed migration.")

    def status(self) -> MigrationStatus:
        plan, events = self._plan()
        snapshots = self._snapshots(plan, events)
        with self._factory() as uow:
            self._check(uow)
            completed = uow.sync_for(plan.target).get_state("migration_complete") is True
        return MigrationStatus(plan, tuple(sorted(snapshots)), completed)

    def _plan(self) -> tuple[MigrationPlan, tuple[Record, ...]]:
        events = self._gateway.read_migration_events()
        if not events:
            raise ValueError("Begin a reviewed migration first.")
        header = events[0]
        if (
            set(header)
            != {
                "kind",
                "schema_version",
                "spreadsheet_id",
                "log_id",
                "participants",
                "legacy_fingerprint",
                "legacy_count",
                "created_at",
            }
            or header["kind"] != "migration_manifest"
            or type(header["schema_version"]) is not int
            or header["schema_version"] != 2
        ):
            raise ValueError("Unsupported migration manifest.")
        participants = header["participants"]
        if (
            not isinstance(participants, (list, tuple))
            or not participants
            or len(set(participants)) != len(participants)
        ):
            raise ValueError("Invalid participant roster.")
        roster = tuple(sorted(_text(p) for p in participants))
        target = SyncTarget(_text(header["spreadsheet_id"]), _text(header["log_id"]))
        fields = {
            "v1_snapshot_row": {"kind", "index", "row"},
            "snapshot_record": {"kind", "participant_id", "fingerprint", "index", "record"},
            "snapshot_ack": {"kind", "participant_id", "fingerprint", "count", "backup_reference"},
            "cutover": {"kind", "log_id", "legacy_fingerprint", "snapshots", "seed_ids"},
        }
        for event in events[1:]:
            kind = event.get("kind")
            if (
                not isinstance(kind, str)
                or kind not in fields
                or set(event) != fields[kind]
                or (kind.startswith("snapshot_") and event.get("participant_id") not in roster)
            ):
                with self._factory() as uow:
                    self._check(uow)
                    uow.sync_for(target).record_problem(
                        SyncProblem(
                            fingerprint(event),
                            "unsupported_migration_event",
                            event,
                            "QI_FLOW_MIGRATION_V2",
                        )
                    )
                raise ValueError(
                    "Unsupported migration event; preserve and review the safety ledger."
                )
        snapshot_rows: dict[int, tuple[object, ...]] = {}
        for event in events[1:]:
            if event.get("kind") != "v1_snapshot_row":
                continue
            index, row = event.get("index"), event.get("row")
            if type(index) is not int or index < 0 or not isinstance(row, (list, tuple)):
                raise ValueError("Invalid frozen V1 snapshot.")
            value = tuple(row)
            if index in snapshot_rows and snapshot_rows[index] != value:
                raise ValueError("The frozen V1 safety copy changed.")
            snapshot_rows[index] = value
        count = header["legacy_count"]
        if type(count) is not int or count < 0 or set(snapshot_rows) != set(range(count)):
            raise ValueError("Incomplete frozen V1 safety copy.")
        rows = tuple(snapshot_rows[i] for i in range(count))
        expected = _text(header["legacy_fingerprint"])
        if fingerprint(rows) != expected:
            raise ValueError("The frozen V1 safety copy failed verification.")
        plan = MigrationPlan(
            SyncTarget(_text(header["spreadsheet_id"]), _text(header["log_id"])),
            roster,
            expected,
            rows,
            utc_instant(datetime.fromisoformat(_text(header["created_at"]))),
        )
        with self._factory() as uow:
            self._check(uow)
            if uow.settings.get("google_sync_sheet_url") is None:
                raise ValueError("Save sync settings before migration.")
            configuration = GoogleSyncConfiguration(
                _text(uow.settings.get("google_sync_sheet_url")),
                _text(uow.settings.get("google_sync_client_id")),
            )
            if configuration.spreadsheet_id != plan.target.spreadsheet_id:
                raise ValueError("Migration belongs to different sync settings.")
        return plan, events

    def begin(self, participants: Sequence[str], *, writers_paused: bool) -> MigrationPlan:
        if not writers_paused:
            raise ValueError("All V1 writers must be paused and upgraded first.")
        roster = tuple(sorted(_text(p.strip()) for p in participants))
        if not roster or len(set(roster)) != len(roster):
            raise ValueError("Declare every participant once.")
        if self._gateway.read_migration_events():
            plan, _ = self._plan()
            if plan.participants != roster:
                raise ValueError("The existing participant roster differs; review that migration.")
            self.verify_legacy_frozen(plan.target)
            return plan
        safety = self._safety.create_snapshot()
        rows = tuple(tuple(row) for row in self._gateway.read_legacy_rows())
        with self._factory() as uow:
            self._check(uow)
            configuration = GoogleSyncConfiguration(
                _text(uow.settings.get("google_sync_sheet_url")),
                _text(uow.settings.get("google_sync_client_id")),
            )
            log_id = uow.settings.get("migration_planned_log")
            if not isinstance(log_id, str):
                log_id = self._identifiers.change_id()
                uow.settings.save("migration_planned_log", log_id, self._clock.now())
            uow.settings.save(
                "migration_initial_backup", safety.backup_reference, self._clock.now()
            )
        target = SyncTarget(configuration.spreadsheet_id, log_id)
        events: list[Record] = [
            {
                "kind": "migration_manifest",
                "schema_version": 2,
                "spreadsheet_id": target.spreadsheet_id,
                "log_id": target.log_id,
                "participants": roster,
                "legacy_fingerprint": fingerprint(rows),
                "legacy_count": len(rows),
                "created_at": utc_instant(self._clock.now()).isoformat(),
            }
        ]
        events.extend(
            {"kind": "v1_snapshot_row", "index": i, "row": row} for i, row in enumerate(rows)
        )
        self._gateway.initialize_migration(target, events)
        plan, _ = self._plan()  # Readback, including identity, before reporting success.
        if plan.target != target:
            raise ValueError("Another migration initialized this workbook; review and join it.")
        return plan

    @staticmethod
    def _snapshots(plan: MigrationPlan, events: Sequence[Record]) -> dict[str, tuple[Record, ...]]:
        result = {}
        for participant in plan.participants:
            acks = [
                e
                for e in events
                if e.get("kind") == "snapshot_ack" and e.get("participant_id") == participant
            ]
            if not acks:
                continue
            fingerprints = {canonical_json(e) for e in acks}
            if len(fingerprints) != 1:
                raise ValueError("Participant acknowledgements disagree; review the migration.")
            ack = acks[0]
            if set(ack) != {"kind", "participant_id", "fingerprint", "count", "backup_reference"}:
                raise ValueError("Invalid participant acknowledgement.")
            _text(ack["backup_reference"])
            records: dict[int, Record] = {}
            for event in events:
                if (
                    event.get("kind") != "snapshot_record"
                    or event.get("participant_id") != participant
                    or event.get("fingerprint") != ack["fingerprint"]
                ):
                    continue
                index = event.get("index")
                if type(index) is not int or index < 0:
                    raise ValueError("Invalid participant snapshot index.")
                value = _mapping(event.get("record"))
                if index in records and records[index] != value:
                    raise ValueError("Participant safety snapshot changed.")
                records[index] = value
            count = ack["count"]
            if type(count) is not int or count < 0 or set(records) != set(range(count)):
                raise ValueError("Incomplete participant snapshot.")
            snapshot = tuple(records[i] for i in range(count))
            if fingerprint(snapshot) != ack["fingerprint"]:
                raise ValueError("Participant snapshot failed verification.")
            result[participant] = snapshot
        return result

    def contribute(self, participant: str) -> MigrationPlan:
        plan, events = self._plan()
        if participant not in plan.participants:
            raise ValueError("Choose a declared participant for this machine.")
        self.verify_legacy_frozen(plan.target)
        snapshots = self._snapshots(plan, events)
        with self._factory() as uow:
            self._check(uow)
            current = local_snapshot(uow)
        if participant in snapshots:
            if fingerprint(snapshots[participant]) != fingerprint(current):
                raise ValueError(
                    "Local snapshot changed; preserve this history and restart "
                    "the reviewed migration."
                )
            return plan
        safety = self._safety.create_snapshot()
        with self._factory() as uow:
            self._check(uow)
            if fingerprint(local_snapshot(uow)) != fingerprint(safety.records):
                raise ValueError("Local snapshot changed during backup; retry review.")
        digest = fingerprint(safety.records)
        outgoing: list[Record] = [
            {
                "kind": "snapshot_record",
                "participant_id": participant,
                "fingerprint": digest,
                "index": i,
                "record": record,
            }
            for i, record in enumerate(safety.records)
        ]
        outgoing.append(
            {
                "kind": "snapshot_ack",
                "participant_id": participant,
                "fingerprint": digest,
                "count": len(safety.records),
                "backup_reference": safety.backup_reference,
            }
        )
        self._gateway.append_migration_events(outgoing)
        verified_plan, verified_events = self._plan()
        if (
            verified_plan != plan
            or self._snapshots(plan, verified_events).get(participant) != safety.records
        ):
            raise ValueError("Participant snapshot acknowledgement could not be verified.")
        return plan

    def verify_legacy_frozen(self, target: SyncTarget) -> None:
        plan, _ = self._plan()
        if plan.target != target:
            raise ValueError("Migration log identity changed.")
        current = self._gateway.read_legacy_rows()
        if fingerprint(current) != plan.legacy_fingerprint:
            with self._factory() as uow:
                self._check(uow)
                uow.sync_for(target).record_problem(
                    SyncProblem(
                        fingerprint(current), "legacy_writes_resumed", current, "QI_FLOW_SYNC_V1"
                    )
                )
            raise ValueError(
                "V1 writes resumed; pause and upgrade every old writer, then review migration."
            )

    def complete(self, participant: str) -> MigrationPlan:
        plan, events = self._plan()
        self.verify_legacy_frozen(plan.target)
        snapshots = self._snapshots(plan, events)
        if participant not in snapshots or set(snapshots) != set(plan.participants):
            raise ValueError(
                "Every declared participant must acknowledge its safety snapshot first."
            )
        with self._factory() as uow:
            self._check(uow)
            if fingerprint(local_snapshot(uow)) != fingerprint(snapshots[participant]):
                raise ValueError(
                    "Local snapshot changed; preserve this history and restart "
                    "the reviewed migration."
                )
        try:
            sources = (_legacy_records(plan.legacy_rows), *snapshots.values())
            seeds = {
                change.change_id: change
                for records in sources
                for change in seed_groups(records, plan)
            }
        except (DomainError, ValueError, TypeError, KeyError, OverflowError) as error:
            with self._factory() as uow:
                self._check(uow)
                uow.sync_for(plan.target).record_problem(
                    SyncProblem(
                        "unsupported-legacy-" + plan.legacy_fingerprint,
                        "unsupported_legacy_snapshot",
                        plan.legacy_rows,
                        "QI_FLOW_SYNC_V1:safety-copy",
                    )
                )
            raise ValueError(
                "Unsupported legacy or participant data; review preserved snapshots before cutover."
            ) from error
        remote = {c.change_id: c for c in self._gateway.read_changes()}
        if self._gateway.read_problems() or any(
            identifier in remote and remote[identifier] != seed
            for identifier, seed in seeds.items()
        ):
            raise ValueError("Migration seed log is invalid; preserve and review it.")
        # Append whole groups even when an interrupted batch left some members visible.
        missing_groups = {c.group_id for identifier, c in seeds.items() if identifier not in remote}
        outgoing = tuple(c for c in seeds.values() if c.group_id in missing_groups)
        if outgoing:
            self._gateway.append_changes(outgoing)
        remote = {c.change_id: c for c in self._gateway.read_changes()}
        if self._gateway.read_problems() or any(
            remote.get(identifier) != seed for identifier, seed in seeds.items()
        ):
            raise ValueError("Migration seed readback is incomplete; retry using stable IDs.")
        cutover: Record = {
            "kind": "cutover",
            "log_id": plan.target.log_id,
            "legacy_fingerprint": plan.legacy_fingerprint,
            "snapshots": {
                name: fingerprint(snapshot) for name, snapshot in sorted(snapshots.items())
            },
            "seed_ids": sorted(seeds),
        }
        prior = [e for e in events if e.get("kind") == "cutover"]
        if prior and any(canonical_json(e) != canonical_json(cutover) for e in prior):
            raise ValueError("The cutover manifest differs; review all snapshots.")
        if not prior:
            self._gateway.append_migration_events((cutover,))
        verified_plan, verified_events = self._plan()
        if verified_plan != plan or not any(
            canonical_json(e) == canonical_json(cutover) for e in verified_events
        ):
            raise ValueError("Cutover completion could not be verified.")
        self.verify_legacy_frozen(plan.target)
        local_seeds = seed_groups(snapshots[participant], plan)
        with self._factory() as uow:
            self._check(uow)
            if fingerprint(local_snapshot(uow)) != fingerprint(snapshots[participant]):
                raise ValueError("Local snapshot changed during cutover; preserve and review it.")
            repo = uow.sync_for(plan.target)
            repo.observe(tuple(remote.values()))
            for change in local_seeds:
                repo.set_heads(change.entity_key, (change.change_id,))
            if repo.problems():
                raise ValueError("Quarantined migration data prevents activation.")
            repo.set_state("migration_complete", True)
            repo.set_state("legacy_fingerprint", plan.legacy_fingerprint)
            repo.set_state("participant_id", participant)
            now = self._clock.now()
            uow.settings.save(
                "google_sync_v2_target",
                {"spreadsheet_id": plan.target.spreadsheet_id, "log_id": plan.target.log_id},
                now,
            )
            uow.settings.save("google_sync_enabled", True, now)
        SyncReconciler(
            self._factory, plan.target, self._clock, self._identifiers, generation=self._generation
        ).reconcile()
        return plan
