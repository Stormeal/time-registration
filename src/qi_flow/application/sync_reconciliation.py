"""Causal reconciliation of durable observations without network or outbox echo."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime

from qi_flow.application.ports import Clock, IdentifierGenerator, SyncRepository, UnitOfWork
from qi_flow.application.sync_capture import active_target
from qi_flow.application.sync_models import (
    EntityKey,
    SyncChange,
    SyncConflict,
    SyncJobObsoleteError,
    SyncTarget,
    parse_json,
    validate_group,
)
from qi_flow.application.sync_payloads import read_payload
from qi_flow.domain.errors import DomainError
from qi_flow.domain.interval_validation import validate_intervals
from qi_flow.domain.models import DayDetails, Deduction, WorkSession


@dataclass(frozen=True)
class ReconciliationResult:
    materialized_count: int
    conflict_count: int
    staged_count: int


@dataclass(frozen=True)
class _Graph:
    changes: Mapping[str, SyncChange]
    tips: Mapping[EntityKey, tuple[SyncChange, ...]]
    group_ancestors: Mapping[str, frozenset[str]]
    staged: tuple[dict[str, str], ...]


def validate_completed_parents(
    changes: Sequence[SyncChange],
    sessions: Mapping[str, WorkSession],
    deductions: Mapping[str, Deduction],
) -> None:
    """Sync may change only completed aggregates, including explicit resolutions."""
    for change in changes:
        if change.entity_kind != "deduction":
            continue
        child = deductions.get(change.entity_id)
        if child is not None and child.deleted_at is None:
            parent = sessions.get(str(child.session_id))
            if parent is None or parent.actual_ended_at is None or parent.deleted_at is not None:
                raise ValueError("Synced deductions require completed live work.")


def _graph(changes: Sequence[SyncChange]) -> _Graph:
    by_id = {change.change_id: change for change in changes}
    groups: dict[str, list[SyncChange]] = {}
    for change in by_id.values():
        groups.setdefault(change.group_id, []).append(change)
    dependencies: dict[str, set[str]] = {}
    staged: list[dict[str, str]] = []
    for group_id, group in groups.items():
        try:
            validate_group(group)
            if len({change.entity_key for change in group}) != len(group):
                raise ValueError("Duplicate entity in one commit.")
            refs: list[tuple[EntityKey, str]] = []
            for change in group:
                refs.extend((change.entity_key, parent) for parent in change.parent_ids)
                refs.extend(
                    (key, head)
                    for key, heads in change.aggregate_base_heads.items()
                    for head in heads
                )
            dependency_groups = set()
            for key, identifier in refs:
                parent = by_id.get(identifier)
                if parent is None or parent.entity_key != key or parent.group_id == group_id:
                    raise ValueError("Missing, mismatched or cyclic ancestry.")
                dependency_groups.add(parent.group_id)
            dependencies[group_id] = dependency_groups
        except ValueError:
            staged.append({"group_id": group_id, "reason": "incomplete_or_invalid_ancestry"})
    accepted: dict[str, frozenset[str]] = {}
    pending = dict(dependencies)
    while pending:
        ready = sorted(
            group_id for group_id, parents in pending.items() if parents <= accepted.keys()
        )
        if not ready:
            staged.extend(
                {"group_id": group_id, "reason": "missing_or_cyclic_ancestry"}
                for group_id in sorted(pending)
            )
            break
        for group_id in ready:
            parents = pending.pop(group_id)
            ancestors = set(parents)
            for ancestor_group in parents:
                ancestors.update(accepted[ancestor_group])
            accepted[group_id] = frozenset(ancestors)
    eligible = {
        identifier: change for identifier, change in by_id.items() if change.group_id in accepted
    }
    superseded = {parent for change in eligible.values() for parent in change.parent_ids}
    tips: dict[EntityKey, list[SyncChange]] = {}
    for identifier, change in eligible.items():
        if identifier not in superseded:
            tips.setdefault(change.entity_key, []).append(change)
    return _Graph(
        by_id,
        {key: tuple(sorted(heads, key=lambda c: c.change_id)) for key, heads in tips.items()},
        accepted,
        tuple(staged),
    )


class _Components:
    def __init__(self, keys: Sequence[EntityKey]) -> None:
        self.parents = {key: key for key in keys}

    def root(self, key: EntityKey) -> EntityKey:
        self.parents.setdefault(key, key)
        while self.parents[key] != key:
            key = self.parents[key]
        return key

    def join(self, left: EntityKey, right: EntityKey) -> None:
        self.parents[self.root(left)] = self.root(right)

    def groups(self) -> tuple[tuple[EntityKey, ...], ...]:
        groups: dict[EntityKey, list[EntityKey]] = {}
        for key in self.parents:
            groups.setdefault(self.root(key), []).append(key)
        return tuple(tuple(sorted(keys)) for _, keys in sorted(groups.items()))


def _components(
    graph: _Graph, deductions: Sequence[Deduction]
) -> tuple[tuple[EntityKey, ...], ...]:
    components = _Components(tuple(graph.tips))
    tip_ids = {change.change_id for heads in graph.tips.values() for change in heads}
    current_groups = {graph.changes[identifier].group_id for identifier in tip_ids}
    first_members: dict[str, EntityKey] = {}
    for change in graph.changes.values():
        if change.entity_kind == "deduction" and change.payload is not None:
            parent = change.payload.get("session_id")
            if isinstance(parent, str):
                components.join(change.entity_key, ("work_session", parent))
        if change.group_id in current_groups:
            first = first_members.setdefault(change.group_id, change.entity_key)
            components.join(first, change.entity_key)
    for deduction in deductions:
        key = ("deduction", str(deduction.id))
        parent = ("work_session", str(deduction.session_id))
        if key in components.parents or parent in components.parents:
            components.join(key, parent)
    return components.groups()


def _close_superseded_conflicts(
    repo: SyncRepository, graph: _Graph, staged_keys: set[EntityKey]
) -> None:
    if repo.problems():
        return  # Quarantined ancestry cannot prove that an old conflict was resolved.
    for conflict in repo.conflicts():
        if set(conflict.entity_keys) & staged_keys:
            continue
        covered, advanced = True, False
        for key in {change.entity_key for change in conflict.changes}:
            tips = graph.tips.get(key, ())
            if len(tips) != 1 or repo.heads(key) != (tips[0].change_id,):
                covered = False
                break
            tip = tips[0]
            ancestry = {tip.change_id}
            pending = list(tip.parent_ids)
            while pending:
                identifier = pending.pop()
                if identifier not in ancestry:
                    ancestry.add(identifier)
                    pending.extend(graph.changes[identifier].parent_ids)
            old_heads = {c.change_id for c in conflict.changes if c.entity_key == key}
            if not old_heads <= ancestry:
                covered = False
                break
            advanced |= old_heads != {tip.change_id}
        if covered and advanced:
            # The materialized heads have passed whole-timesheet validation in this UoW.
            repo.close_conflict(conflict.conflict_id, conflict.head_ids)


class SyncReconciler:
    def __init__(
        self,
        factory: Callable[[], UnitOfWork],
        target: SyncTarget,
        clock: Clock,
        identifiers: IdentifierGenerator,
        *,
        generation: int | None = None,
    ) -> None:
        self._factory, self._target, self._clock, self._identifiers = (
            factory,
            target,
            clock,
            identifiers,
        )
        self._generation = generation

    def _conflict(
        self,
        repo: SyncRepository,
        keys: Sequence[EntityKey],
        changes: Sequence[SyncChange],
        reason: str,
    ) -> None:
        keys = tuple(sorted(set(keys)))
        unique = {change.change_id: change for change in changes}
        existing = next(
            (
                conflict
                for conflict in repo.conflicts()
                if conflict.entity_keys == keys and conflict.reason == reason
            ),
            None,
        )
        identifier = existing.conflict_id if existing else self._identifiers.conflict_id()
        repo.save_conflict(
            SyncConflict(identifier, keys, tuple(unique[key] for key in sorted(unique)), reason)
        )

    def reconcile(self) -> ReconciliationResult:
        now = self._clock.now()
        materialized = 0
        with self._factory() as uow:
            if self._generation is not None and (
                uow.settings.get("google_sync_generation") != self._generation
                or active_target(uow) != self._target
            ):
                raise SyncJobObsoleteError("Sync settings changed before reconciliation.")
            repo = uow.sync_for(self._target)
            graph = _graph(repo.observed())
            staged = list(graph.staged)
            blocked_ids: set[str] = set()
            invalid_manifest = False
            for problem in repo.problems():
                staged.append({"group_id": problem.problem_id, "reason": "quarantined_data"})
                invalid_manifest |= problem.reason == "invalid_log_manifest"
                raw = problem.raw
                if isinstance(raw, tuple) and len(raw) == 1 and isinstance(raw[0], str):
                    try:
                        raw = parse_json(raw[0])
                    except (ValueError, TypeError, RecursionError):
                        raw = None
                if isinstance(raw, Mapping):
                    identifier = raw.get("change_id")
                    if isinstance(identifier, str):
                        blocked_ids.add(identifier)
            blocked_groups = {
                graph.changes[identifier].group_id
                for identifier in blocked_ids
                if identifier in graph.changes
            }
            staged_groups = {entry["group_id"] for entry in staged}
            staged_keys = {
                change.entity_key
                for change in graph.changes.values()
                if change.group_id in staged_groups
            }
            sessions = {str(session.id): session for session in uow.sessions.list_all()}
            deductions = {str(deduction.id): deduction for deduction in uow.deductions.list_all()}
            days = {details.work_date.isoformat(): details for details in uow.days.list_all()}
            for keys in _components(graph, tuple(deductions.values())):
                heads = tuple(change for key in keys for change in graph.tips.get(key, ()))
                if not heads:
                    continue
                if invalid_manifest or any(
                    change.group_id in blocked_groups
                    or bool(graph.group_ancestors[change.group_id] & blocked_groups)
                    for change in heads
                ):
                    continue
                if set(keys) & staged_keys:
                    continue
                if any(len(graph.tips.get(key, ())) > 1 for key in keys):
                    self._conflict(repo, keys, heads, "concurrent_edit")
                    continue
                group_ids = {
                    change.group_id
                    for change in heads
                    if change.entity_kind in {"work_session", "deduction"}
                }
                group_work: dict[str, set[EntityKey]] = {
                    identifier: set() for identifier in group_ids
                }
                for change in graph.changes.values():
                    if change.group_id not in group_work:
                        continue
                    if change.entity_kind == "work_session":
                        group_work[change.group_id].add(change.entity_key)
                    elif change.entity_kind == "deduction":
                        parent_id = (
                            change.payload.get("session_id") if change.payload is not None else None
                        )
                        local_child = deductions.get(change.entity_id)
                        if not isinstance(parent_id, str) and local_child is not None:
                            parent_id = str(local_child.session_id)
                        if isinstance(parent_id, str):
                            group_work[change.group_id].add(("work_session", parent_id))
                concurrent = any(
                    left != right
                    and bool(group_work[left] & group_work[right])
                    and left not in graph.group_ancestors[right]
                    and right not in graph.group_ancestors[left]
                    for left in group_ids
                    for right in group_ids
                )
                if concurrent:
                    self._conflict(repo, keys, heads, "concurrent_aggregate_edit")
                    continue
                applicable = [
                    change
                    for change in heads
                    if repo.heads(change.entity_key) != (change.change_id,)
                ]
                if not applicable:
                    continue
                candidate_sessions = {key: replace(value) for key, value in sessions.items()}
                candidate_deductions = {key: replace(value) for key, value in deductions.items()}
                candidate_days = dict(days)
                try:
                    for change in applicable:
                        self._apply_candidate(
                            change, candidate_sessions, candidate_deductions, candidate_days, now
                        )
                    validate_completed_parents(applicable, candidate_sessions, candidate_deductions)
                    validate_intervals(
                        tuple(candidate_sessions.values()),
                        tuple(candidate_deductions.values()),
                        as_of=now,
                    )
                except (DomainError, ValueError, TypeError, KeyError, OverflowError):
                    competing = list(heads)
                    affected = list(keys)
                    for identifier, local in sessions.items():
                        if local.deleted_at is not None or local.actual_ended_at is None:
                            continue
                        for change in applicable:
                            proposed = (
                                candidate_sessions.get(change.entity_id)
                                if change.entity_kind == "work_session"
                                else None
                            )
                            if (
                                proposed is not None
                                and proposed.id != local.id
                                and proposed.actual_ended_at is not None
                                and proposed.actual_started_at < local.actual_ended_at
                                and proposed.actual_ended_at > local.actual_started_at
                            ):
                                key = ("work_session", identifier)
                                affected.append(key)
                                competing.extend(graph.tips.get(key, ()))
                    self._conflict(repo, affected, competing, "invalid_aggregate")
                    continue
                # No domain writes happen until the complete candidate validates.
                for change in sorted(
                    applicable, key=lambda item: item.entity_kind != "work_session"
                ):
                    identifier = change.entity_id
                    if change.entity_kind == "work_session" and identifier in candidate_sessions:
                        value = candidate_sessions[identifier]
                        if identifier in sessions:
                            uow.sessions.save(value)
                        else:
                            uow.sessions.add(value)
                    elif change.entity_kind == "deduction" and identifier in candidate_deductions:
                        deduction = candidate_deductions[identifier]
                        if identifier in deductions:
                            uow.deductions.save(deduction)
                        else:
                            uow.deductions.add(deduction)
                    elif change.entity_kind == "day_details":
                        if identifier in candidate_days:
                            uow.days.save(candidate_days[identifier])
                        else:
                            uow.days.delete(date.fromisoformat(identifier))
                    repo.set_heads(change.entity_key, (change.change_id,))
                    materialized += 1
                sessions, deductions, days = (
                    candidate_sessions,
                    candidate_deductions,
                    candidate_days,
                )
            _close_superseded_conflicts(repo, graph, staged_keys)
            repo.set_state("staged_reconciliation", staged)
            return ReconciliationResult(materialized, len(repo.conflicts()), len(staged))

    @staticmethod
    def _apply_candidate(
        change: SyncChange,
        sessions: dict[str, WorkSession],
        deductions: dict[str, Deduction],
        days: dict[str, DayDetails],
        now: datetime,
    ) -> None:
        kind, identifier = change.entity_key
        if kind not in {"work_session", "deduction", "day_details"}:
            raise ValueError("Unknown V2 entity kind.")
        if kind == "day_details" and date.fromisoformat(identifier).isoformat() != identifier:
            raise ValueError("V2 day IDs must be canonical ISO dates.")
        if kind == "work_session":
            existing = sessions.get(identifier)
            if existing is not None and existing.is_active:
                raise ValueError("Remote changes cannot replace local running work.")
        if kind == "deduction":
            previous = deductions.get(identifier)
            if previous is not None and previous.is_active:
                raise ValueError("Remote changes cannot replace a local running deduction.")
        if change.operation == "upsert":
            assert change.payload is not None
            entity = read_payload(kind, identifier, change.payload)
            if isinstance(entity, WorkSession):
                sessions[identifier] = entity
            elif isinstance(entity, Deduction):
                previous = deductions.get(identifier)
                if previous is not None and previous.session_id != entity.session_id:
                    raise ValueError("A deduction cannot change its parent.")
                deductions[identifier] = entity
            else:
                days[identifier] = entity
        elif kind == "work_session" and identifier in sessions:
            sessions[identifier].deleted_at = now
            sessions[identifier].updated_at = now
        elif kind == "deduction" and identifier in deductions:
            deductions[identifier].deleted_at = now
            deductions[identifier].updated_at = now
        elif kind == "day_details":
            if change.operation == "withdraw":
                raise ValueError("Day details cannot be withdrawn as active work.")
            days.pop(identifier, None)
