"""Immutable V2 sync envelopes and canonical wire representation.

These DTOs validate protocol structure, not timesheet business aggregates or ancestry.
Incomplete observations can be stored; only complete groups may be published/materialized.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Literal, cast

type EntityKey = tuple[str, str]
type Operation = Literal["upsert", "delete", "withdraw"]


def _identifier(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Sync identifiers must be nonempty strings.")
    return value


def _ids(value: object) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)):
        raise ValueError("Sync identifiers must be a sequence.")
    identifiers = tuple(_identifier(item) for item in value)
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("Sync identifiers must not repeat.")
    return tuple(sorted(identifiers))


def utc_instant(value: datetime) -> datetime:
    """Normalize an explicit aware instant without consulting the local timezone."""
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("Sync timestamps must be timezone-aware.")
    return value.astimezone(UTC)


def freeze_json(value: object) -> object:
    """Copy JSON-compatible input into recursively immutable owned values."""
    try:
        return _freeze_json(value, set())
    except RecursionError as error:
        raise ValueError("Sync JSON nesting is too deep.") from error


def _freeze_json(value: object, ancestors: set[int]) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Sync JSON must contain only finite numbers.")
        return value
    if isinstance(value, (Mapping, list, tuple)):
        identity = id(value)
        if identity in ancestors:
            raise ValueError("Sync JSON cannot contain cyclic references.")
        ancestors.add(identity)
        try:
            if isinstance(value, Mapping):
                if any(not isinstance(key, str) for key in value):
                    raise ValueError("Sync JSON object keys must be strings.")
                return MappingProxyType(
                    {key: _freeze_json(item, ancestors) for key, item in value.items()}
                )
            return tuple(_freeze_json(item, ancestors) for item in value)
        finally:
            ancestors.remove(identity)
    raise ValueError("Sync content must be JSON-compatible.")


def _plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _plain_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain_json(item) for item in value]
    return value


def canonical_json(value: object) -> str:
    """Encode JSON deterministically, rejecting lossy or non-JSON input."""
    return json.dumps(
        _plain_json(freeze_json(value)),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Sync JSON contains duplicate object keys.")
        result[key] = value
    return result


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ValueError("Expected a sync JSON object.")
    return value


def parse_json(raw: str) -> object:
    """Parse without silently losing duplicate keys or accepting non-JSON numbers."""
    value = json.loads(raw, object_pairs_hook=_object)
    freeze_json(value)
    return value


@dataclass(frozen=True)
class SyncTarget:
    spreadsheet_id: str
    log_id: str

    def __post_init__(self) -> None:
        _identifier(self.spreadsheet_id)
        _identifier(self.log_id)


@dataclass(frozen=True)
class SyncChange:
    change_id: str
    schema_version: int
    entity_kind: str
    entity_id: str
    parent_ids: tuple[str, ...]
    group_id: str
    group_members: tuple[str, ...]
    group_digest: str
    aggregate_base_heads: Mapping[EntityKey, tuple[str, ...]]
    operation: Operation
    payload: Mapping[str, object] | None
    created_at: datetime
    device_id: str

    def __post_init__(self) -> None:
        for identifier in (
            self.change_id,
            self.entity_kind,
            self.entity_id,
            self.group_id,
            self.device_id,
        ):
            _identifier(identifier)
        if type(self.schema_version) is not int or self.schema_version != 2:
            raise ValueError("Unsupported sync schema version.")
        if not isinstance(self.group_digest, str):
            raise ValueError("Sync group digest must be a string.")
        if self.operation not in ("upsert", "delete", "withdraw"):
            raise ValueError("Unknown sync operation.")
        if self.operation == "upsert":
            payload = freeze_json(_mapping(self.payload))
            object.__setattr__(self, "payload", payload)
        elif self.payload is not None:
            raise ValueError("Delete and withdraw changes must have null payloads.")
        object.__setattr__(self, "parent_ids", _ids(self.parent_ids))
        object.__setattr__(self, "group_members", _ids(self.group_members))
        object.__setattr__(self, "created_at", utc_instant(self.created_at))
        if not isinstance(self.aggregate_base_heads, Mapping):
            raise ValueError("Aggregate bases must be a mapping.")
        bases: dict[EntityKey, tuple[str, ...]] = {}
        for key, heads in self.aggregate_base_heads.items():
            if not isinstance(key, tuple) or len(key) != 2:
                raise ValueError("Aggregate base keys must be entity kind/id pairs.")
            bases[(_identifier(key[0]), _identifier(key[1]))] = _ids(heads)
        object.__setattr__(
            self, "aggregate_base_heads", MappingProxyType(dict(sorted(bases.items())))
        )

    @property
    def entity_key(self) -> EntityKey:
        return self.entity_kind, self.entity_id

    def to_record(self, *, include_group_digest: bool = True) -> dict[str, object]:
        record: dict[str, object] = {
            "change_id": self.change_id,
            "schema_version": self.schema_version,
            "entity_kind": self.entity_kind,
            "entity_id": self.entity_id,
            "parent_ids": self.parent_ids,
            "group_id": self.group_id,
            "group_members": self.group_members,
            "aggregate_base_heads": [
                {"entity_kind": key[0], "entity_id": key[1], "head_ids": heads}
                for key, heads in self.aggregate_base_heads.items()
            ],
            "operation": self.operation,
            "payload": self.payload,
            "created_at": self.created_at.isoformat(),
            "device_id": self.device_id,
        }
        if include_group_digest:
            record["group_digest"] = self.group_digest
        return record

    def canonical_json(self) -> str:
        return canonical_json(self.to_record())

    def content_digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @classmethod
    def from_json(cls, raw: str) -> SyncChange:
        try:
            record = _mapping(parse_json(raw))
            expected = {
                "change_id",
                "schema_version",
                "entity_kind",
                "entity_id",
                "parent_ids",
                "group_id",
                "group_members",
                "group_digest",
                "aggregate_base_heads",
                "operation",
                "payload",
                "created_at",
                "device_id",
            }
            if set(record) != expected:
                raise ValueError("Sync change fields do not match schema V2.")
            bases: dict[EntityKey, tuple[str, ...]] = {}
            records = record["aggregate_base_heads"]
            if not isinstance(records, list):
                raise ValueError("Aggregate bases must be encoded as records.")
            for value in records:
                base = _mapping(value)
                if set(base) != {"entity_kind", "entity_id", "head_ids"}:
                    raise ValueError("Invalid aggregate base record.")
                key = (_identifier(base["entity_kind"]), _identifier(base["entity_id"]))
                if key in bases:
                    raise ValueError("Duplicate aggregate base record.")
                bases[key] = _ids(base["head_ids"])
            stamp = record["created_at"]
            if not isinstance(stamp, str):
                raise ValueError("Invalid sync timestamp.")
            return cls(
                change_id=_identifier(record["change_id"]),
                schema_version=cast(int, record["schema_version"]),
                entity_kind=_identifier(record["entity_kind"]),
                entity_id=_identifier(record["entity_id"]),
                parent_ids=_ids(record["parent_ids"]),
                group_id=_identifier(record["group_id"]),
                group_members=_ids(record["group_members"]),
                group_digest=cast(str, record["group_digest"]),
                aggregate_base_heads=bases,
                operation=cast(Operation, record["operation"]),
                payload=cast(Mapping[str, object] | None, record["payload"]),
                created_at=datetime.fromisoformat(stamp),
                device_id=_identifier(record["device_id"]),
            )
        except (TypeError, KeyError, OverflowError) as error:
            raise ValueError("Malformed sync change.") from error


def _group_digest(changes: Sequence[SyncChange]) -> str:
    members = [
        change.to_record(include_group_digest=False)
        for change in sorted(changes, key=lambda item: item.change_id)
    ]
    return hashlib.sha256(canonical_json(members).encode("utf-8")).hexdigest()


def validate_group(changes: Sequence[SyncChange]) -> None:
    """Verify completeness and the immutable commit envelope before consuming it."""
    if not changes:
        raise ValueError("A complete sync group must contain members.")
    first = changes[0]
    actual = tuple(sorted(change.change_id for change in changes))
    if len(set(actual)) != len(actual) or actual != first.group_members:
        raise ValueError("A complete sync group must contain exactly its listed members.")
    for change in changes:
        if (
            change.group_id != first.group_id
            or change.group_members != first.group_members
            or change.aggregate_base_heads != first.aggregate_base_heads
            or change.group_digest != first.group_digest
        ):
            raise ValueError("Sync group members disagree on their envelope.")
    if first.group_digest != _group_digest(changes):
        raise ValueError("Sync group content does not match its digest.")


def finalize_group(changes: Sequence[SyncChange]) -> tuple[SyncChange, ...]:
    """Fill membership/digest on local drafts with one shared group ID and base."""
    if not changes:
        raise ValueError("Cannot finalize an empty group.")
    members = _ids(tuple(change.change_id for change in changes))
    prepared = tuple(
        replace(change, group_members=members, group_digest="")
        for change in sorted(changes, key=lambda item: item.change_id)
    )
    digest = _group_digest(prepared)
    result = tuple(replace(change, group_digest=digest) for change in prepared)
    validate_group(result)
    return result


@dataclass(frozen=True)
class SyncConflict:
    conflict_id: str
    entity_keys: tuple[EntityKey, ...]
    changes: tuple[SyncChange, ...]
    reason: str

    def __post_init__(self) -> None:
        _identifier(self.conflict_id)
        _identifier(self.reason)
        keys = tuple(
            sorted(set((_identifier(key[0]), _identifier(key[1])) for key in self.entity_keys))
        )
        object.__setattr__(self, "entity_keys", keys)
        object.__setattr__(self, "changes", tuple(self.changes))

    @property
    def head_ids(self) -> frozenset[str]:
        return frozenset(change.change_id for change in self.changes)


@dataclass(frozen=True)
class SyncPublication:
    not_before: datetime | None = None
    attempted: bool = False

    def __post_init__(self) -> None:
        if self.not_before is not None:
            object.__setattr__(self, "not_before", utc_instant(self.not_before))


@dataclass(frozen=True)
class SyncProblem:
    """A durable raw row/response that cannot safely become a SyncChange.

    Use a stable problem ID (for example a hash of raw content and its source).
    Store malformed JSON as its original string; never discard unparseable cells.
    """

    problem_id: str
    reason: str
    raw: object
    source: str = ""

    def __post_init__(self) -> None:
        _identifier(self.problem_id)
        _identifier(self.reason)
        if not isinstance(self.source, str):
            raise ValueError("Sync problem source must be a string.")
        object.__setattr__(self, "raw", freeze_json(self.raw))


class SyncContentError(ValueError):
    """One stable ID was reused for different immutable content."""


class SyncReviewError(ValueError):
    """Conflict heads changed after the user reviewed them."""


class SyncJobObsoleteError(ValueError):
    """The destination or consent changed after this job was created."""


class SyncJobCancelledError(ValueError):
    """Cancellation left any uncertain publication durably pending."""
