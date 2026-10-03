"""Aggregate rules shared by local correction and synchronization."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from qi_flow.domain.errors import DomainError
from qi_flow.domain.interval_validation import validate_intervals
from qi_flow.domain.models import Deduction, DeductionId, DeductionKind, SessionId, WorkSession


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 15, hour, minute, tzinfo=UTC)


def work(start: int = 8, end: int | None = 16, name: str = "work") -> WorkSession:
    return WorkSession(SessionId(name), at(start), at(end) if end is not None else None)


def lunch(start: int = 12, end: int | None = 13, name: str = "lunch") -> Deduction:
    return Deduction(
        DeductionId(name),
        SessionId("work"),
        DeductionKind.LUNCH,
        at(start),
        at(end) if end is not None else None,
    )


@pytest.mark.parametrize(
    "sessions,deductions",
    [
        ([work(8, None), work(9, None, "other")], []),
        ([work(8, None)], [lunch(12, None), lunch(13, None, "other")]),
        ([], [lunch()]),
        ([work(8, 12), work(11, 16, "other")], []),
        ([work(8, None), work(9, 10, "other")], []),
        ([work()], [lunch(12, 14), lunch(13, 15, "other")]),
        ([work(8, None)], [lunch(12, None), lunch(13, 14, "other")]),
        ([work()], [lunch(7, 9)]),
        ([work()], [lunch(15, 17)]),
        ([work()], [lunch(12, None)]),
    ],
    ids=[
        "multiple-active-work",
        "multiple-active-deductions",
        "missing-parent",
        "completed-work-overlap",
        "open-work-overlap",
        "deduction-overlap",
        "open-deduction-overlap",
        "deduction-before-parent",
        "deduction-after-parent",
        "open-deduction-completed-parent",
    ],
)
def test_invalid_live_aggregate_is_rejected(
    sessions: list[WorkSession], deductions: list[Deduction]
) -> None:
    with pytest.raises(DomainError):
        validate_intervals(sessions, deductions, as_of=at(18))


@pytest.mark.parametrize("entry", [work(), lunch()], ids=["work", "deduction"])
@pytest.mark.parametrize(
    "changes",
    [
        {"actual_started_at": at(19), "actual_ended_at": None},
        {"actual_ended_at": at(19)},
        {"actual_ended_at": at(7)},
        {"actual_ended_at": at(8)},
        {"actual_started_at": datetime(2026, 9, 15, 8)},
        {"rounding_minutes": 7},
        {"effective_started_at": at(8)},
        {"effective_ended_at": at(16)},
        {"effective_started_at": at(8), "effective_ended_at": at(8)},
        {"effective_started_at": at(9), "effective_ended_at": at(8)},
        {"effective_started_at": datetime(2026, 9, 15, 8), "effective_ended_at": at(16)},
        {"actual_ended_at": None, "effective_started_at": at(8), "effective_ended_at": at(16)},
    ],
)
def test_invalid_mutated_boundaries_are_rejected(
    entry: WorkSession | Deduction, changes: dict[str, datetime | int | None]
) -> None:
    # Restored before-images mutate entities after their constructor validation.
    candidate = replace(entry)
    for field, value in changes.items():
        setattr(candidate, field, value)
    sessions = [candidate] if isinstance(candidate, WorkSession) else [work()]
    deductions = [candidate] if isinstance(candidate, Deduction) else []
    with pytest.raises(DomainError):
        validate_intervals(sessions, deductions, as_of=at(18))


def test_touching_work_and_deductions_are_valid() -> None:
    validate_intervals(
        [work(8, 16), work(16, None, "other")],
        [lunch(12, 13), lunch(13, 14, "other")],
        as_of=at(18),
    )


def test_just_started_work_and_lunch_are_valid_at_as_of() -> None:
    validate_intervals([work(8, None)], [lunch(8, None)], as_of=at(8))


def test_soft_deleted_records_do_not_participate_in_validation() -> None:
    deleted_work = work(7, None, "deleted")
    deleted_work.deleted_at = at(17)
    deleted_work.actual_started_at = at(19)
    deleted_lunch = lunch(12, None, "deleted")
    deleted_lunch.deleted_at = at(17)
    deleted_lunch.session_id = SessionId("missing")
    validate_intervals([work(), deleted_work], [lunch(), deleted_lunch], as_of=at(18))


def test_deleted_parent_does_not_make_a_live_deduction_valid() -> None:
    parent = work()
    parent.deleted_at = at(17)
    with pytest.raises(DomainError):
        validate_intervals([parent], [lunch()], as_of=at(18))


def test_valid_rounded_bounds_may_extend_past_press_and_actual_parent() -> None:
    parent = WorkSession(SessionId("work"), at(8, 2), at(16, 2), at(8), at(16, 5))
    child = Deduction(
        DeductionId("lunch"),
        parent.id,
        DeductionKind.LUNCH,
        at(8, 2),
        at(8, 8),
        at(8),
        at(8, 10),
    )
    validate_intervals([parent], [child], as_of=at(16, 2))


def test_effective_overlap_does_not_reject_touching_actual_work() -> None:
    first = work(8, 9)
    first.actual_ended_at = at(9, 2)
    first.effective_started_at, first.effective_ended_at = at(8), at(9, 5)
    second = work(9, 16, "other")
    second.actual_started_at = at(9, 2)
    second.effective_started_at, second.effective_ended_at = at(9), at(16)
    validate_intervals([first, second], [], as_of=at(18))


def test_naive_as_of_is_rejected_with_a_domain_error() -> None:
    with pytest.raises(DomainError):
        validate_intervals([], [], as_of=at(18).replace(tzinfo=None))


@pytest.mark.parametrize("reverse", [False, True])
def test_duplicate_live_work_identity_is_rejected(reverse: bool) -> None:
    sessions = [work(8, 12), work(10, 16)]
    if reverse:
        sessions.reverse()
    with pytest.raises(DomainError):
        validate_intervals(sessions, [], as_of=at(18))


def test_duplicate_live_deduction_identity_is_rejected() -> None:
    with pytest.raises(DomainError):
        validate_intervals([work()], [lunch(10, 11), lunch(12, 13)], as_of=at(18))
