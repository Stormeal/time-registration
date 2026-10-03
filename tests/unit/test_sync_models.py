"""Canonical protocol content must remain stable across adapters and retries."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from qi_flow.application.sync_models import (
    SyncChange,
    SyncProblem,
    canonical_json,
    finalize_group,
    validate_group,
)


def draft(identifier: str = "c1", **overrides: object) -> SyncChange:
    values = dict(
        change_id=identifier,
        schema_version=2,
        entity_kind="work_session",
        entity_id="s1",
        parent_ids=(),
        group_id="g1",
        group_members=(identifier,),
        group_digest="",
        aggregate_base_heads={},
        operation="upsert",
        payload={"note": "æ", "nested": [{"value": 1}]},
        created_at=datetime(2026, 10, 3, 8, tzinfo=UTC),
        device_id="opaque-device",
    )
    values.update(overrides)
    return SyncChange(**values)  # type: ignore[arg-type]


def test_canonical_content_owns_nested_data_and_normalizes_order_and_utc() -> None:
    nested = {"b": [{"value": 1}], "a": 2}
    bases = {("work_session", "s1"): ["p2", "p1"]}
    change = draft(payload=nested, aggregate_base_heads=bases, parent_ids=("p2", "p1"))
    before = change.canonical_json()
    nested["b"][0]["value"] = 99  # type: ignore[index]
    bases[("work_session", "s1")].append("p3")
    same = draft(
        payload={"a": 2, "b": [{"value": 1}]},
        aggregate_base_heads={("work_session", "s1"): ("p1", "p2")},
        parent_ids=("p1", "p2"),
        created_at=datetime(2026, 10, 3, 10, tzinfo=timezone(timedelta(hours=2))),
    )
    assert change.canonical_json() == before == same.canonical_json()
    assert change.content_digest() == same.content_digest()
    assert SyncChange.from_json(before) == change
    assert json.loads(before)["aggregate_base_heads"] == [
        {"entity_kind": "work_session", "entity_id": "s1", "head_ids": ["p1", "p2"]}
    ]
    with pytest.raises(TypeError):
        change.payload["a"] = 9  # type: ignore[index]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), {1: "bad"}, {1}, object()])
def test_non_json_payloads_are_rejected(value: object) -> None:
    with pytest.raises(ValueError):
        draft(payload={"bad": value})


def test_group_validation_checks_members_envelope_and_content_digest() -> None:
    group = finalize_group((draft("c2", entity_id="s2"), draft("c1")))
    assert [item.change_id for item in group] == ["c1", "c2"]
    assert all(item.group_members == ("c1", "c2") for item in group)
    validate_group(group)
    with pytest.raises(ValueError, match="complete"):
        validate_group(group[:1])
    with pytest.raises(ValueError, match="digest"):
        validate_group((replace(group[0], payload={"changed": True}), group[1]))
    with pytest.raises(ValueError, match="envelope"):
        validate_group((replace(group[0], aggregate_base_heads={("x", "y"): ("p",)}), group[1]))
    with pytest.raises(ValueError):
        finalize_group((draft(), draft()))


def test_wire_parser_rejects_unknown_schema_fields_and_duplicate_keys() -> None:
    raw = json.loads(draft().canonical_json())
    raw["schema_version"] = 3
    with pytest.raises(ValueError):
        SyncChange.from_json(json.dumps(raw))
    raw["schema_version"] = 2
    raw["unexpected"] = "lost otherwise"
    with pytest.raises(ValueError):
        SyncChange.from_json(json.dumps(raw))
    with pytest.raises(ValueError):
        SyncChange.from_json('{"change_id":"one","change_id":"two"}')


@pytest.mark.parametrize("operation", ["delete", "withdraw"])
def test_tombstone_has_no_payload_and_naive_instants_are_rejected(operation: str) -> None:
    with pytest.raises(ValueError):
        draft(operation=operation)
    assert draft(operation=operation, payload=None).payload is None
    with pytest.raises(ValueError):
        draft(created_at=datetime(2026, 10, 3))


def test_quarantine_owns_raw_malformed_row_without_interpreting_it() -> None:
    raw = {"cells": ["{bad json", "=formula"]}
    problem = SyncProblem("issue", "malformed_json", raw, "row:4")
    raw["cells"].clear()
    assert canonical_json(problem.raw) == '{"cells":["{bad json","=formula"]}'


def test_cyclic_input_is_refused_as_non_json_without_exhausting_recursion() -> None:
    cyclic: dict[str, object] = {}
    cyclic["cycle"] = cyclic
    with pytest.raises(ValueError, match="JSON"):
        draft(payload=cyclic)
