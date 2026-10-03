"""DSB application review safety with in-memory ports and no GUI/browser dependencies."""

from contextlib import contextmanager
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from qi_flow.application.dsb import DsbService
from qi_flow.application.testhuset import FillDecision, HourSlot
from qi_flow.domain.models import IsoWeek, SessionId, WorkSession
from qi_flow.domain.testhuset import ProjectTask

TASK = ProjectTask("11-22", "DSB", "Allocation A")
OTHER = ProjectTask("33-44", "DSB", "Allocation B")
WEEK = IsoWeek(2026, 38)


class Sheet:
    def __init__(self):
        self.values = {14: "", 15: "2.00", 16: "8,00", 17: "0.00", 18: "unrelated"}
        self.writes = []
        self.attempts = []
        self.reads = []
        self.commits = 0
        self.fail_at = None
        self.fail_commit = False

    def scan(self, week):
        raise AssertionError("DSB filling must not rescan allocation defaults")

    def read(self, slot):
        self.reads.append(slot)
        return self.values[slot.work_date.day]

    def write_verified(self, slot):
        self.attempts.append(slot)
        if len(self.attempts) == self.fail_at:
            raise ValueError("Uncertain save")
        self.writes.append(slot)
        self.values[slot.work_date.day] = slot.hours

    def commit_verified(self):
        self.commits += 1
        if self.fail_commit:
            raise ValueError("Uncertain Send")


@pytest.fixture
def setup():
    sessions = [
        WorkSession(
            SessionId(str(day)),
            datetime(2026, 9, day, 7, tzinfo=UTC),
            datetime(2026, 9, day, 15, tzinfo=UTC),
        )
        for day in (14, 15, 16, 17)
    ]
    settings = {
        "dsb_default_task": TASK.id,
        "testhuset_default_task": TASK.id,
        "dsb_included_testhuset_tasks": [TASK.id],
    }
    ports = SimpleNamespace(
        settings=SimpleNamespace(
            get=settings.get, save=lambda key, value, now: settings.__setitem__(key, value)
        ),
        sessions=SimpleNamespace(list_intersecting=lambda start, end: sessions),
        deductions=SimpleNamespace(list_for_session=lambda session_id: []),
    )

    @contextmanager
    def uow():
        yield ports

    clock = SimpleNamespace(now=lambda: datetime(2026, 11, 1, tzinfo=UTC))
    cache = SimpleNamespace(load=lambda: (TASK, OTHER))
    service = DsbService(uow, clock, SimpleNamespace(), cache, testhuset_cache=cache)
    return service, Sheet(), sessions, settings


def test_only_included_resolved_branches_contribute_and_exclusions_remain_inspectable(setup):
    service, sheet, sessions, _ = setup
    sessions[0].testhuset_task_id = TASK.id
    sessions[1].testhuset_task_id = OTHER.id
    sessions[2].testhuset_task_id = "99-99"
    preview = service.preview(sheet, WEEK)
    assert [(item.proposed.work_date.day, item.proposed.hours) for item in preview.slots] == [
        (14, "8.00"),
        (17, "8.00"),
    ]
    assert preview.coverage.included_seconds == 16 * 3600
    assert preview.coverage.excluded_seconds == 16 * 3600
    excluded = [entry for entry in preview.coverage.entries if not entry.included]
    assert [(entry.work_date.day, entry.branch_id, entry.reason) for entry in excluded] == [
        (15, OTHER.id, "not_included"),
        (16, "99-99", "unresolved"),
    ]
    assert len(sessions) == 4  # Filtering never changes local history.


def test_empty_allowlist_blocks_before_any_external_read_or_write(setup):
    service, sheet, _, settings = setup
    settings["dsb_included_testhuset_tasks"] = []
    with pytest.raises(ValueError, match=r"Include.*branch"):
        service.preview(sheet, WEEK)
    assert sheet.reads == sheet.attempts == []
    assert sheet.commits == 0


def test_allowlist_version_change_invalidates_even_identical_rounded_totals(setup):
    service, sheet, _, _ = setup
    preview = service.preview(sheet, WEEK)
    service.set_included_branches({TASK.id, OTHER.id})
    with pytest.raises(ValueError, match="changed"):
        service.fill(
            sheet,
            preview,
            {0: FillDecision.REPLACE, 1: FillDecision.KEEP, 3: FillDecision.KEEP},
            confirmed=True,
        )
    assert sheet.attempts == []


def test_active_deleted_and_unassigned_sessions_are_excluded(setup):
    service, _, sessions, settings = setup
    settings.pop("testhuset_default_task")
    sessions[0].testhuset_task_id = TASK.id
    sessions[1].actual_ended_at = None
    sessions[2].deleted_at = datetime(2026, 10, 1, tzinfo=UTC)
    coverage = service.coverage(WEEK)
    assert coverage.included_seconds == 8 * 3600
    assert coverage.excluded_seconds == 8 * 3600
    assert coverage.entries[-1].reason == "unresolved"


def test_selection_uses_scanned_ids_and_never_infers_project_names(setup):
    service, _, _, settings = setup
    settings.pop("dsb_included_testhuset_tasks")
    assert service.included_branches() == frozenset()
    with pytest.raises(ValueError, match="scanned"):
        service.set_included_branches({"88-88"})
    service.set_included_branches({OTHER.id})
    assert settings["dsb_included_testhuset_tasks"] == [OTHER.id]


@pytest.mark.parametrize(
    "decisions",
    [
        {},
        {0: FillDecision.REPLACE},
        {0: FillDecision.REPLACE, 1: None, 3: FillDecision.KEEP},
        {0: FillDecision.REPLACE, 1: "replace", 3: FillDecision.KEEP},
        {0: FillDecision.REPLACE, 1: True, 3: FillDecision.KEEP},
        {0: FillDecision.REPLACE, 1: FillDecision.KEEP, 2: FillDecision.KEEP, 3: FillDecision.KEEP},
        {0: FillDecision.REPLACE, 1: FillDecision.KEEP, 3: FillDecision.KEEP, 4: FillDecision.KEEP},
        {False: FillDecision.REPLACE, 1: FillDecision.KEEP, 3: FillDecision.KEEP},
        {0.0: FillDecision.REPLACE, 1: FillDecision.KEEP, 3: FillDecision.KEEP},
        frozenset({0, 1, 3}),
    ],
)
def test_missing_or_invalid_decisions_never_reach_external_writes(setup, decisions):
    service, sheet, _, _ = setup
    preview = service.preview(sheet, WEEK)
    before_reads = list(sheet.reads)

    with pytest.raises(ValueError, match="decision"):
        service.fill(sheet, preview, decisions, confirmed=True)

    assert sheet.reads == before_reads
    assert sheet.attempts == []
    assert sheet.commits == 0
    # Invalid input must allow the user to complete this same review.
    result = service.fill(
        sheet,
        preview,
        {0: FillDecision.KEEP, 1: FillDecision.KEEP, 3: FillDecision.KEEP},
        confirmed=True,
    )
    assert (result.changed, result.kept, result.matched) == (0, 3, 1)


def test_only_explicit_replacements_are_written_and_sent_once(setup):
    service, sheet, _, _ = setup
    preview = service.preview(sheet, WEEK)
    result = service.fill(
        sheet,
        preview,
        {0: FillDecision.REPLACE, 1: FillDecision.KEEP, 3: FillDecision.REPLACE},
        confirmed=True,
    )
    assert (result.changed, result.kept, result.matched) == (2, 1, 1)
    assert sheet.writes == [
        HourSlot(date(2026, 9, 14), TASK, "8.00"),
        HourSlot(date(2026, 9, 17), TASK, "8.00"),
    ]
    assert sheet.values == {14: "8.00", 15: "2.00", 16: "8,00", 17: "8.00", 18: "unrelated"}
    assert sheet.commits == 1
    with pytest.raises(ValueError, match="new preview"):
        service.fill(
            sheet,
            preview,
            {0: FillDecision.REPLACE, 1: FillDecision.KEEP, 3: FillDecision.REPLACE},
            confirmed=True,
        )
    assert sheet.commits == 1


@pytest.mark.parametrize("change", ["assignment", "default", "local_hours", "remote"])
def test_stale_review_requires_a_new_preview_before_writing(setup, change):
    service, sheet, sessions, settings = setup
    preview = service.preview(sheet, WEEK)
    if change == "assignment":
        sessions[0].dsb_allocation_id = OTHER.id
    elif change == "default":
        settings["dsb_default_task"] = OTHER.id
    elif change == "local_hours":
        sessions[0].actual_ended_at = datetime(2026, 9, 14, 16, tzinfo=UTC)
    else:
        sheet.values[14] = "2.00"
    choices = {0: FillDecision.REPLACE, 1: FillDecision.KEEP, 3: FillDecision.KEEP}
    with pytest.raises(ValueError, match="changed"):
        service.fill(sheet, preview, choices, confirmed=True)
    with pytest.raises(ValueError, match="new preview"):
        service.fill(sheet, preview, choices, confirmed=True)
    assert sheet.attempts == []
    assert sheet.commits == 0


def test_unconfirmed_fill_has_no_side_effects_and_does_not_consume_review(setup):
    service, sheet, _, _ = setup
    preview = service.preview(sheet, WEEK)
    choices = {0: FillDecision.REPLACE, 1: FillDecision.KEEP, 3: FillDecision.KEEP}
    with pytest.raises(ValueError, match="Confirm"):
        service.fill(sheet, preview, choices, confirmed=False)
    assert sheet.attempts == []
    assert sheet.commits == 0
    assert service.fill(sheet, preview, choices, confirmed=True).changed == 1


@pytest.mark.parametrize("fail_at", [1, 2])
def test_uncertain_or_partial_save_stops_and_consumes_review(setup, fail_at):
    service, sheet, _, _ = setup
    preview = service.preview(sheet, WEEK)
    choices = {0: FillDecision.REPLACE, 1: FillDecision.REPLACE, 3: FillDecision.REPLACE}
    sheet.fail_at = fail_at
    with pytest.raises(ValueError, match="Uncertain"):
        service.fill(sheet, preview, choices, confirmed=True)
    assert len(sheet.attempts) == fail_at
    assert len(sheet.writes) == fail_at - 1
    assert sheet.commits == 0
    sheet.fail_at = None
    with pytest.raises(ValueError, match="new preview"):
        service.fill(sheet, preview, choices, confirmed=True)
    assert len(sheet.attempts) == fail_at
    fresh = service.preview(sheet, WEEK)
    fresh_choices = {1: FillDecision.REPLACE, 3: FillDecision.KEEP}
    if fail_at == 1:
        fresh_choices[0] = FillDecision.KEEP
    service.fill(sheet, fresh, fresh_choices, confirmed=True)
    assert sheet.commits == 1


def test_uncertain_send_is_not_retried_from_the_same_review(setup):
    service, sheet, _, _ = setup
    preview = service.preview(sheet, WEEK)
    choices = {0: FillDecision.REPLACE, 1: FillDecision.KEEP, 3: FillDecision.KEEP}
    sheet.fail_commit = True
    with pytest.raises(ValueError, match="Uncertain Send"):
        service.fill(sheet, preview, choices, confirmed=True)
    with pytest.raises(ValueError, match="new preview"):
        service.fill(sheet, preview, choices, confirmed=True)
    assert len(sheet.attempts) == 1
    assert sheet.commits == 1


def test_new_review_invalidates_identical_previous_preview(setup):
    service, sheet, _, _ = setup
    old = service.preview(sheet, WEEK)
    fresh = service.preview(sheet, WEEK)
    assert old == fresh and old is not fresh
    with pytest.raises(ValueError, match="new preview"):
        service.fill(
            sheet,
            old,
            {0: FillDecision.KEEP, 1: FillDecision.KEEP, 3: FillDecision.KEEP},
            confirmed=True,
        )
    assert sheet.attempts == []
