"""Malformed remote business records remain reviewable through explicit recovery commands."""

from datetime import UTC, datetime

import pytest
from PySide6.QtCore import Qt

from qi_flow.application.google_sync_service import SyncService
from qi_flow.application.sync_models import SyncChange, SyncTarget, finalize_group
from qi_flow.application.sync_reconciliation import SyncReconciler
from qi_flow.infrastructure.sqlite.database import SQLiteDatabase
from qi_flow.infrastructure.sqlite.repositories import SQLiteUnitOfWork
from qi_flow.infrastructure.system import UuidIdentifierGenerator
from qi_flow.ui.sync_conflict_dialog import SyncConflictDialog

NOW = datetime(2026, 10, 3, 12, tzinfo=UTC)
TARGET = SyncTarget("sheet", "log")
KEY = ("work_session", "missing-endpoint")


class Clock:
    def now(self):
        return NOW


@pytest.mark.parametrize(
    "payload",
    [
        {"actual_started_at": "2026-10-02T07:00:00+00:00"},
        {
            "actual_started_at": "9999-12-31T23:30:00+00:00",
            "actual_ended_at": "9999-12-31T23:45:00+00:00",
        },
    ],
)
def test_missing_endpoint_import_opens_review_and_explicit_deletion_recovers(
    qtbot, tmp_path, payload
):
    database = SQLiteDatabase(tmp_path / "invalid-review.sqlite3")
    database.initialize()

    def factory():
        return SQLiteUnitOfWork(database)

    malformed = finalize_group(
        (
            SyncChange(
                "missing-endpoint",
                2,
                KEY[0],
                KEY[1],
                (),
                "g-invalid",
                (),
                "",
                {},
                "upsert",
                payload,
                NOW,
                "other-device",
            ),
        )
    )[0]
    with factory() as uow:
        uow.settings.save("google_sync_generation", 1, NOW)
        uow.settings.save("google_sync_enabled", True, NOW)
        uow.settings.save(
            "google_sync_v2_target", {"spreadsheet_id": "sheet", "log_id": "log"}, NOW
        )
        repo = uow.sync_for(TARGET)
        repo.set_state("migration_complete", True)
        repo.observe((malformed,))
    # Reviewing and resolving use local durable state; no network gateway method is needed.
    service = SyncService(
        factory, object(), TARGET, Clock(), UuidIdentifierGenerator(), generation=1
    )
    SyncReconciler(factory, TARGET, Clock(), UuidIdentifierGenerator(), generation=1).reconcile()
    with factory() as uow:
        conflict = uow.sync_for(TARGET).conflicts()[0]
    reviewed = service.review(conflict.conflict_id)
    dialog = SyncConflictDialog(reviewed)
    qtbot.addWidget(dialog)
    captured = []
    dialog.resolution_requested.connect(lambda *args: captured.append(args))
    dialog.choices[KEY].setCurrentIndex(dialog.choices[KEY].count() - 1)
    qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    assert len(captured) == 1
    identifier, heads, payloads = captured[0]
    service.resolve(identifier, heads, payloads)
    with factory() as uow:
        repo = uow.sync_for(TARGET)
        assert repo.conflicts() == ()
        assert repo.pending()[0].operation == "delete"
        assert malformed in repo.observed()
        assert uow.sessions.list_all() == []
