"""Real SQLite safety copies and synthetic Sheets requests protect cutover ownership."""

import importlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from qi_flow.application.google_sync import GoogleSyncConfiguration, GoogleSyncSettings
from qi_flow.application.sync_actions import GoogleSyncActions
from qi_flow.application.sync_migration import fingerprint
from qi_flow.application.sync_models import SyncChange, SyncTarget, finalize_group
from qi_flow.infrastructure.google_oauth import GoogleOAuthStore
from qi_flow.infrastructure.google_sheets_sync import GoogleSheetsSync
from qi_flow.infrastructure.sqlite.database import SQLiteDatabase
from qi_flow.infrastructure.sqlite.repositories import SQLiteUnitOfWork

NOW = datetime(2026, 10, 3, 12, tzinfo=UTC)
CONFIG = GoogleSyncConfiguration(
    "https://docs.google.com/spreadsheets/d/sheet/edit", "test.apps.googleusercontent.com"
)
TARGET = SyncTarget("sheet", "log")


class Request:
    def __init__(self, action):
        self.action = action

    def execute(self):
        return self.action()


class Sheets:
    def __init__(self):
        self.tabs = {
            7: {"title": "Personal", "rows": [["=SUM(A1:A2)"]]},
            8: {"title": "QI_FLOW_SYNC_V1", "rows": []},
        }
        self.requests = []
        self.before_batch = lambda: None
        self.lost_response = False

    def spreadsheets(self):
        return self

    def values(self):
        return self

    def get(self, **kwargs):
        if "range" in kwargs:
            title = kwargs["range"].split("!")[0]
            return Request(
                lambda: {
                    "values": [
                        row[:]
                        for tab in self.tabs.values()
                        if tab["title"] == title
                        for row in tab["rows"]
                    ]
                }
            )
        return Request(
            lambda: {
                "sheets": [
                    {"properties": {"sheetId": i, "title": t["title"]}}
                    for i, t in self.tabs.items()
                ]
            }
        )

    def batchUpdate(self, **kwargs):
        def apply():
            self.before_batch()
            self.requests.append(kwargs)
            titles = {t["title"] for t in self.tabs.values()}
            adds = [
                r["addSheet"]["properties"] for r in kwargs["body"]["requests"] if "addSheet" in r
            ]
            if any(p["title"] in titles or p["sheetId"] in self.tabs for p in adds):
                raise ValueError("Tab already exists; atomic request rejected")
            for props in adds:
                self.tabs[props["sheetId"]] = {"title": props["title"], "rows": []}
            for request in kwargs["body"]["requests"]:
                if "appendCells" in request:
                    append = request["appendCells"]
                    assert append["fields"] == "userEnteredValue"
                    self.tabs[append["sheetId"]]["rows"].extend(
                        [
                            [c["userEnteredValue"]["stringValue"] for c in r["values"]]
                            for r in append["rows"]
                        ]
                    )
            if self.lost_response:
                raise TimeoutError("Atomic create succeeded, response lost")
            return {}

        return Request(apply)


def manifest(log="log"):
    return {
        "kind": "migration_manifest",
        "schema_version": 2,
        "spreadsheet_id": "sheet",
        "log_id": log,
        "participants": ["a"],
        "legacy_fingerprint": fingerprint([]),
        "legacy_count": 0,
        "created_at": NOW.isoformat(),
    }


def adapter(sheet, target=None):
    return GoogleSheetsSync(CONFIG, None, target=target, service_factory=lambda: sheet)


def change(identifier="ordinary"):
    return finalize_group(
        (
            SyncChange(
                identifier,
                2,
                "day_details",
                "2026-10-02",
                (),
                "group-" + identifier,
                (),
                "",
                {},
                "upsert",
                {"location": "home", "note": "=SUM(A1:A2)", "revision": 1},
                NOW,
                "device",
            ),
        )
    )


def test_initialize_preserves_existing_tabs_and_refuses_concurrent_replacement():
    sheet = Sheets()
    a, b = adapter(sheet), adapter(sheet)
    a.initialize_migration(TARGET, (manifest(),))
    original = {
        i: {"title": t["title"], "rows": [r[:] for r in t["rows"]]} for i, t in sheet.tabs.items()
    }
    assert a.read_migration_events()[0] == manifest()
    assert sheet.tabs[7]["rows"] == [["=SUM(A1:A2)"]]
    assert sheet.tabs[8]["rows"] == []
    with pytest.raises(ValueError):
        b.initialize_migration(SyncTarget("sheet", "other-log"), (manifest("other-log"),))
    assert sheet.tabs == original
    assert a.read_changes() == ()
    assert len(sheet.requests) == 1


def test_race_between_read_and_atomic_create_does_not_overwrite_the_winner():
    sheet = Sheets()
    loser = adapter(sheet)
    winner = adapter(sheet)

    def race():
        sheet.before_batch = lambda: None
        winner.initialize_migration(SyncTarget("sheet", "winner"), (manifest("winner"),))

    sheet.before_batch = race
    with pytest.raises(ValueError):
        loser.initialize_migration(TARGET, (manifest(),))
    assert adapter(sheet).read_migration_events()[0]["log_id"] == "winner"
    assert sheet.tabs[7]["rows"] == [["=SUM(A1:A2)"]]


def test_accepted_initialization_lost_response_can_be_read_without_creating_again():
    sheet = Sheets()
    sheet.lost_response = True
    with pytest.raises(TimeoutError):
        adapter(sheet).initialize_migration(TARGET, (manifest(),))
    sheet.lost_response = False
    joined = adapter(sheet)
    assert joined.read_migration_events()[0] == manifest()
    assert joined.read_changes() == ()
    assert len(sheet.requests) == 1


def test_only_migration_seeds_are_appendable_until_verified_cutover():
    sheet = Sheets()
    sync = adapter(sheet)
    sync.initialize_migration(TARGET, (manifest(),))
    with pytest.raises(ValueError, match="cutover"):
        sync.append_changes(change())
    sync.append_changes(change("seed-one"))
    assert [c.change_id for c in sync.read_changes()] == ["seed-one"]


def test_adapter_refuses_ordinary_append_if_legacy_rows_change_after_cutover():
    sheet = Sheets()
    sync = adapter(sheet)
    sync.initialize_migration(TARGET, (manifest(),))
    sync.append_migration_events(
        (
            {
                "kind": "cutover",
                "log_id": "log",
                "legacy_fingerprint": fingerprint([]),
                "snapshots": {"a": fingerprint(())},
                "seed_ids": [],
            },
        )
    )
    sync.append_changes(change())
    sheet.tabs[8]["rows"] = [["new V1 write"]]
    with pytest.raises(ValueError, match="V1"):
        sync.append_changes(change("second"))
    assert sync.read_problems()[0].reason == "legacy_writes_resumed"
    rows = next(t["rows"] for t in sheet.tabs.values() if t["title"] == "QI_FLOW_SYNC_V2")
    assert len(rows) == 2


def test_pull_detects_renewed_v1_writes_before_materializing_or_acking():
    sheet = Sheets()
    sync = adapter(sheet)
    sync.initialize_migration(TARGET, (manifest(),))
    sheet.tabs[8]["rows"] = [["new V1 write"]]
    with pytest.raises(ValueError, match="V1"):
        sync.read_changes()
    assert sync.read_problems()[0].reason == "legacy_writes_resumed"


def test_wrong_log_identity_refuses_migration_append():
    sheet = Sheets()
    adapter(sheet).initialize_migration(TARGET, (manifest(),))
    sync = adapter(sheet, SyncTarget("sheet", "other-log"))
    with pytest.raises(ValueError, match="identity"):
        sync.append_migration_events(({"kind": "snapshot_ack"},))
    assert len(sheet.requests) == 1


def test_real_migration_safety_copy_is_verified_and_preserves_active_state(tmp_path):
    module = importlib.import_module("qi_flow.infrastructure.sync_migration_backup")
    database = SQLiteDatabase(tmp_path / "live.sqlite3")
    database.initialize()
    from qi_flow.domain.models import SessionId, WorkSession

    with SQLiteUnitOfWork(database) as uow:
        uow.sessions.add(WorkSession(SessionId("active"), NOW, created_at=NOW, updated_at=NOW))
    safety = module.SQLiteMigrationSafety(database, tmp_path / "safety")
    snapshot = safety.create_snapshot()
    assert snapshot.records == ()
    with SQLiteUnitOfWork(SQLiteDatabase(Path(snapshot.backup_reference))) as uow:
        assert uow.sessions.get_active().id == "active"
    assert safety.create_snapshot().backup_reference != snapshot.backup_reference


def test_schedule_authorization_can_read_saved_client_without_a_nested_write_lock(
    tmp_path, monkeypatch
):
    database = SQLiteDatabase(tmp_path / "schedule.sqlite3")
    database.initialize()

    def factory():
        return SQLiteUnitOfWork(database)

    class Clock:
        def now(self):
            return NOW

    settings = GoogleSyncSettings(factory, Clock())
    settings.save(CONFIG)
    with factory() as uow:
        uow.settings.save("google_sync_enabled", True, NOW)
        uow.settings.save(
            "google_sync_v2_target", {"spreadsheet_id": "sheet", "log_id": "log"}, NOW
        )
        uow.sync_for(TARGET).set_state("migration_complete", True)
    values = {
        "refresh-token": json.dumps({"client_id": CONFIG.oauth_client_id}),
        "desktop-client": json.dumps({"installed": {"client_id": CONFIG.oauth_client_id}}),
    }
    monkeypatch.setattr(
        "qi_flow.infrastructure.google_oauth.keyring.get_password",
        lambda service, account: values.get(account),
    )
    connect = SQLiteDatabase.connect

    def no_lock_wait(database):
        connection = connect(database)
        connection.execute("PRAGMA busy_timeout = 0")
        return connection

    monkeypatch.setattr(SQLiteDatabase, "connect", no_lock_wait)
    oauth = GoogleOAuthStore(client_id=lambda: settings.load().oauth_client_id)
    actions = GoogleSyncActions(factory, settings, oauth, Clock(), None, None)
    expected_binding = (TARGET, settings.generation())

    for _ in range(3):
        assert actions.schedule_state().binding == expected_binding
