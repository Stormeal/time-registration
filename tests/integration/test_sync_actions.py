"""Production application facade exposes durable status without importing UI/adapters."""

import importlib
from datetime import UTC, datetime, timedelta

import pytest

from qi_flow.application.google_sync import GoogleSyncConfiguration, GoogleSyncSettings
from qi_flow.application.google_sync_service import SyncResult
from qi_flow.application.sync_models import SyncChange, SyncTarget, finalize_group
from qi_flow.infrastructure.sqlite.database import SQLiteDatabase
from qi_flow.infrastructure.sqlite.repositories import SQLiteUnitOfWork

NOW = datetime(2026, 10, 3, 12, tzinfo=UTC)
TARGET = SyncTarget("sheet", "log")


class Clock:
    def now(self):
        return NOW


@pytest.fixture
def rig(tmp_path):
    module = importlib.import_module("qi_flow.application.sync_actions")
    database = SQLiteDatabase(tmp_path / "actions.sqlite3")
    database.initialize()

    def factory():
        return SQLiteUnitOfWork(database)

    settings = GoogleSyncSettings(factory, Clock())
    settings.save(
        GoogleSyncConfiguration(
            "https://docs.google.com/spreadsheets/d/sheet/edit", "test.apps.googleusercontent.com"
        )
    )

    class OAuth:
        authorized = True

        def is_authorized(self):
            return self.authorized

    class Service:
        def __init__(self):
            self.calls = []

        def run_once(self, **kwargs):
            self.calls.append(kwargs)
            return SyncResult(2, 0, None, "pending")

        def review(self, identifier):
            return "review-" + identifier

        def resolve(self, *args):
            self.calls.append(args)

    oauth, service = OAuth(), Service()
    created = []

    def service_factory(configuration, target, generation, cancelled):
        created.append((configuration, target, generation, cancelled))
        return service

    actions = module.GoogleSyncActions(
        factory, settings, oauth, Clock(), service_factory, lambda *args: None
    )

    def enable():
        with factory() as uow:
            uow.settings.save("google_sync_enabled", True, NOW)
            uow.settings.save(
                "google_sync_v2_target", {"spreadsheet_id": "sheet", "log_id": "log"}, NOW
            )
            uow.sync_for(TARGET).set_state("migration_complete", True)

    return actions, factory, settings, oauth, service, created, enable


def test_no_unreviewed_target_can_create_a_network_service(rig):
    actions, _, _, _, _, created, _ = rig
    assert actions.status().state == "migration_required"
    with pytest.raises(ValueError, match="migration"):
        actions.synchronize(lambda: False)
    assert created == []


def test_authorization_required_keeps_durable_pending_count_and_prior_success(rig):
    actions, factory, _, oauth, _, _, enable = rig
    enable()
    change = finalize_group(
        (
            SyncChange(
                "pending",
                2,
                "day_details",
                "2026-10-02",
                (),
                "group",
                (),
                "",
                {},
                "upsert",
                {"location": "office", "note": "", "revision": 1},
                NOW,
                "device",
            ),
        )
    )
    with factory() as uow:
        repo = uow.sync_for(TARGET)
        repo.enqueue(change)
        repo.set_state("last_success", NOW.isoformat())
    oauth.authorized = False
    status = actions.status()
    assert status.state == "authorization_required"
    assert status.pending_count == 1
    assert status.last_success == NOW
    with pytest.raises(ValueError, match="Authorize"):
        actions.synchronize(lambda: False)


def test_job_factory_receives_current_reviewed_binding_and_deadline(rig):
    actions, _, settings, _, service, created, enable = rig
    enable()

    def cancelled():
        return False

    result = actions.synchronize(cancelled)
    assert result.state == "pending"
    assert created[0][1:3] == (TARGET, settings.generation())
    assert created[0][3] is cancelled
    assert service.calls[-1]["deadline"] == NOW + timedelta(minutes=2)


def test_configuration_change_invalidates_cached_review_service(rig):
    actions, _, settings, _, _, created, enable = rig
    enable()
    assert actions.review("one") == "review-one"
    assert actions.review("two") == "review-two"
    assert len(created) == 1
    settings.disable()
    with pytest.raises(ValueError, match="migration"):
        actions.resolve("one", frozenset(), {})
