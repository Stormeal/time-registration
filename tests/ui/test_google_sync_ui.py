"""Conflict review presents provenance and requires explicit choices for each entry."""

import importlib
from datetime import UTC, datetime

from qi_flow.application.sync_models import (
    SyncChange,
    SyncConflict,
    SyncConflictReview,
    finalize_group,
)

KEY = ("day_details", "2026-10-02")


def reviewed():
    def version(identifier, note, parents=()):
        return finalize_group(
            (
                SyncChange(
                    identifier,
                    2,
                    *KEY,
                    parents,
                    "g-" + identifier,
                    (),
                    "",
                    {},
                    "upsert",
                    {"location": "office", "note": note, "revision": 1},
                    datetime(2026, 10, 3, 12, tzinfo=UTC),
                    "device",
                ),
            )
        )[0]

    base, a, b = (
        version("base", "base note"),
        version("a", "first note", ("base",)),
        version("b", "second note", ("base",)),
    )
    return SyncConflictReview(
        SyncConflict("conflict", (KEY,), (a, b), "concurrent_edit"),
        {KEY: {"location": "office", "note": "local note", "revision": 1}},
        {KEY: ("base",)},
        "local-fingerprint",
        (base,),
    )


def dialog(qtbot):
    module = importlib.import_module("qi_flow.ui.sync_conflict_dialog")
    view = module.SyncConflictDialog(reviewed())
    qtbot.addWidget(view)
    return view


def test_versions_and_causal_base_are_visible_without_preselected_choice(qtbot):
    from PySide6.QtWidgets import QLabel

    view = dialog(qtbot)
    text = "\n".join(label.text() for label in view.findChildren(QLabel))
    for note in ("local note", "base note", "first note", "second note"):
        assert note in text
    assert view.choices[KEY].currentIndex() == 0
    assert not view.save_button.isEnabled()


def test_explicit_local_choice_emits_reviewed_heads_and_owned_payload(qtbot):
    view = dialog(qtbot)
    view.choices[KEY].setCurrentIndex(1)
    with qtbot.waitSignal(view.resolution_requested) as emitted:
        view.save_button.click()
    conflict_id, heads, payloads = emitted.args
    assert conflict_id == "conflict"
    assert heads == frozenset({"a", "b"})
    assert payloads[KEY]["note"] == "local note"
    assert not view.save_button.isEnabled()


def test_explicit_deletion_emits_null_and_cancel_never_emits_a_resolution(qtbot):
    view = dialog(qtbot)
    view.choices[KEY].setCurrentIndex(view.choices[KEY].count() - 1)
    with qtbot.waitSignal(view.resolution_requested) as emitted:
        view.save_button.click()
    assert emitted.args[2] == {KEY: None}
    another = dialog(qtbot)
    signals = []
    another.resolution_requested.connect(lambda *args: signals.append(args))
    another.reject()
    assert signals == []


def test_failed_validation_keeps_review_and_choices_available(qtbot):
    view = dialog(qtbot)
    view.choices[KEY].setCurrentIndex(1)
    view.save_button.click()
    view.show_failure("The selected entries overlap; correct or delete one.")
    assert "overlap" in view.status_label.text()
    assert view.choices[KEY].currentIndex() == 1
    assert view.save_button.isEnabled()


def test_invalid_incoming_date_is_reviewable_and_can_be_deleted(qtbot):
    from dataclasses import replace

    module = importlib.import_module("qi_flow.ui.sync_conflict_dialog")
    original = reviewed()
    key = ("work_session", "bad-work")
    invalid = finalize_group(
        (
            replace(
                original.conflict.changes[0],
                entity_kind=key[0],
                entity_id=key[1],
                payload={"actual_started_at": "bad", "actual_ended_at": "bad"},
            ),
        )
    )[0]
    view = module.SyncConflictDialog(
        SyncConflictReview(
            SyncConflict("invalid", (key,), (invalid,), "invalid_aggregate"),
            {key: None},
            {key: ()},
            "fingerprint",
            (),
        )
    )
    qtbot.addWidget(view)
    view.choices[key].setCurrentIndex(view.choices[key].count() - 1)
    with qtbot.waitSignal(view.resolution_requested) as emitted:
        view.save_button.click()
    assert emitted.args[2] == {key: None}


def test_corrected_explicit_dates_emit_manual_exact_minute_bounds(qtbot):
    from dataclasses import replace

    module = importlib.import_module("qi_flow.ui.sync_conflict_dialog")
    original = reviewed()
    key = ("work_session", "work")
    payload = {
        "actual_started_at": "2026-10-02T07:00:00+00:00",
        "actual_ended_at": "2026-10-02T09:00:00+00:00",
        "effective_started_at": None,
        "effective_ended_at": None,
        "source": "timer",
        "created_at": "2026-10-02T07:00:00+00:00",
        "updated_at": "2026-10-02T09:00:00+00:00",
        "deleted_at": None,
        "rounding_minutes": 5,
        "revision": 2,
        "testhuset_task_id": None,
        "dsb_allocation_id": None,
    }
    incoming = finalize_group(
        (
            replace(
                original.conflict.changes[0], entity_kind=key[0], entity_id=key[1], payload=payload
            ),
        )
    )[0]
    view = module.SyncConflictDialog(
        SyncConflictReview(
            SyncConflict("work-conflict", (key,), (incoming,), "invalid_aggregate"),
            {key: payload},
            {key: ()},
            "fingerprint",
            (),
        )
    )
    qtbot.addWidget(view)
    view.choices[key].setCurrentIndex(1)
    check, start, end = view.corrections[key]
    check.setChecked(True)
    start.set_value(datetime(2026, 10, 2, 7, tzinfo=UTC))
    end.set_value(datetime(2026, 10, 2, 8, tzinfo=UTC))
    with qtbot.waitSignal(view.resolution_requested) as emitted:
        view.save_button.click()
    corrected = emitted.args[2][key]
    assert corrected["actual_ended_at"] == "2026-10-02T08:00:00+00:00"
    assert corrected["effective_ended_at"] == corrected["actual_ended_at"]
    assert corrected["source"] == "manual"
