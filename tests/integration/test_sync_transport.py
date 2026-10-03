"""Immutable append/readback transport with temporary databases and a shared fake Sheet."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

import qi_flow.application.google_sync_service as sync
from qi_flow.application.google_sync import GoogleSyncConfiguration
from qi_flow.application.sync_models import SyncChange, SyncTarget, canonical_json, finalize_group
from qi_flow.infrastructure.google_sheets_sync import GoogleSheetsSync
from qi_flow.infrastructure.sqlite.database import SQLiteDatabase
from qi_flow.infrastructure.sqlite.repositories import SQLiteUnitOfWork

NOW = datetime(2026, 10, 3, 12, tzinfo=UTC)
TARGET = SyncTarget("sheet", "log")


class Clock:
    def now(self):
        return NOW


def group(identifier="c1", **overrides):
    change = SyncChange(
        identifier,
        2,
        "day_details",
        identifier,
        (),
        "g-" + identifier,
        (),
        "",
        {},
        "upsert",
        {"note": "=SUM(A1:A2)"},
        NOW,
        "device",
    )
    return finalize_group((replace(change, **overrides),))


class Request:
    def __init__(self, action):
        self.action = action

    def execute(self):
        return self.action()


class Sheets:
    def __init__(self):
        self.rows = [[canonical_json({"kind": "manifest", "schema_version": 2, "log_id": "log"})]]
        self.user_tab = [["=SUM(A1:A2)", "user content"]]
        self.requests = []
        self.on_append = lambda: None
        self.fail = None

    def spreadsheets(self):
        return self

    def values(self):
        return self

    def get(self, **kwargs):
        if "range" in kwargs:
            return Request(lambda: {"values": [row[:] for row in self.rows]})
        return Request(
            lambda: {
                "sheets": [
                    {"properties": {"sheetId": 17, "title": "QI_FLOW_SYNC_V2"}},
                    {"properties": {"sheetId": 18, "title": "Personal"}},
                ]
            }
        )

    def batchUpdate(self, **kwargs):
        def apply():
            self.requests.append(kwargs)
            body = kwargs["body"]
            assert set(body) == {"requests"}
            assert len(body["requests"]) == 1
            request = body["requests"][0]["appendCells"]
            assert request["sheetId"] == 17
            assert request["fields"] == "userEnteredValue"
            if self.fail == "reject":
                raise OSError("synthetic quota rejection")
            for row in request["rows"]:
                value = row["values"][0]["userEnteredValue"]
                assert set(value) == {"stringValue"}
                self.rows.append([value["stringValue"]])
            self.on_append()
            if self.fail == "timeout":
                raise TimeoutError("synthetic lost append response")
            return {}

        return Request(apply)


def gateway(sheet):
    return GoogleSheetsSync(
        GoogleSyncConfiguration(
            "https://docs.google.com/spreadsheets/d/sheet/edit", "client.apps.googleusercontent.com"
        ),
        None,
        target=TARGET,
        service_factory=lambda: sheet,
    )


def client(tmp_path, name, changes):
    database = SQLiteDatabase(tmp_path / (name + ".sqlite3"))
    database.initialize()

    def factory():
        return SQLiteUnitOfWork(database)

    with factory() as uow:
        uow.settings.save("google_sync_enabled", True, NOW)
        uow.settings.save("google_sync_generation", 1, NOW)
        uow.settings.save(
            "google_sync_v2_target", {"spreadsheet_id": "sheet", "log_id": "log"}, NOW
        )
        uow.sync_for(TARGET).set_state("migration_complete", True)
        uow.sync_for(TARGET).enqueue(changes)
    return factory


def publisher(factory, sheet):
    return sync.SyncPublicationService(factory, gateway(sheet), TARGET, Clock(), generation=1)


def publish(service):
    return service.publish_once(cancelled=lambda: False, deadline=NOW + timedelta(minutes=2))


@pytest.mark.parametrize("order", [("a", "b"), ("b", "a")])
def test_two_publishers_converge_to_union_without_replacing_other_tabs(tmp_path, order):
    sheet = Sheets()
    factories = {name: client(tmp_path, name, group(name)) for name in order}
    for name in order:
        publish(publisher(factories[name], sheet))
    for name in order:
        publish(publisher(factories[name], sheet))
        with factories[name]() as uow:
            assert {c.change_id for c in uow.sync_for(TARGET).observed()} == {"a", "b"}
            assert uow.sync_for(TARGET).pending() == ()
    assert sheet.user_tab == [["=SUM(A1:A2)", "user content"]]
    assert {c.change_id for c in gateway(sheet).read_changes()} == {"a", "b"}


def test_accepted_append_lost_response_restart_verifies_same_ids_before_ack(tmp_path):
    sheet = Sheets()
    factory = client(tmp_path, "a", group())
    sheet.fail = "timeout"
    with pytest.raises(TimeoutError):
        publish(publisher(factory, sheet))
    with factory() as uow:
        assert uow.sync_for(TARGET).pending() == group()
        assert uow.sync_for(TARGET).publication("c1").attempted
    sheet.fail = None
    assert publish(publisher(factory, sheet)) == 1
    assert len(sheet.requests) == 1
    with factory() as uow:
        assert uow.sync_for(TARGET).pending() == ()


def test_local_change_during_network_stays_pending_and_uses_no_database_lock(tmp_path):
    sheet = Sheets()
    factory = client(tmp_path, "a", group())

    def local_change():
        with factory() as uow:
            uow.sync_for(TARGET).enqueue(group("c2", entity_id="c1", parent_ids=("c1",)))

    sheet.on_append = local_change
    assert publish(publisher(factory, sheet)) == 1
    with factory() as uow:
        assert [c.change_id for c in uow.sync_for(TARGET).pending()] == ["c2"]


def test_configuration_changed_during_append_never_consumes_obsolete_result(tmp_path):
    sheet = Sheets()
    factory = client(tmp_path, "a", group())

    def change_target():
        with factory() as uow:
            uow.settings.save("google_sync_generation", 2, NOW)
            uow.settings.save(
                "google_sync_v2_target", {"spreadsheet_id": "other", "log_id": "other-log"}, NOW
            )

    sheet.on_append = change_target
    with pytest.raises(sync.SyncJobObsoleteError):
        publish(publisher(factory, sheet))
    with factory() as uow:
        assert uow.sync_for(TARGET).pending() == group()
        assert uow.sync_for(SyncTarget("other", "other-log")).observed() == ()


@pytest.mark.parametrize("raw", ["{bad", canonical_json({"schema_version": 99}), "=SUM(A1:A2)"])
def test_invalid_raw_rows_are_quarantined_and_block_publication(tmp_path, raw):
    sheet = Sheets()
    sheet.rows.append([raw])
    factory = client(tmp_path, "a", group())
    with pytest.raises(ValueError):
        publish(publisher(factory, sheet))
    with factory() as uow:
        assert uow.sync_for(TARGET).problems()[0].raw == [raw] or uow.sync_for(TARGET).problems()[
            0
        ].raw == (raw,)
        assert uow.sync_for(TARGET).pending() == group()
    assert sheet.requests == []


def test_duplicate_content_is_idempotent_but_differing_ids_remain_quarantined(tmp_path):
    sheet = Sheets()
    sheet.rows += [[group()[0].canonical_json()]] * 2
    assert len({c.change_id for c in gateway(sheet).read_changes()}) == 1
    sheet.rows.append([group(payload={"note": "different"})[0].canonical_json()])
    factory = client(tmp_path, "a", group())
    with pytest.raises(ValueError):
        publish(publisher(factory, sheet))
    with factory() as uow:
        assert uow.sync_for(TARGET).problems()
        assert uow.sync_for(TARGET).pending() == group()
    assert sheet.requests == []


def test_recreated_log_identity_refuses_append_without_altering_rows():
    sheet = Sheets()
    sheet.rows[0] = [
        canonical_json({"kind": "manifest", "schema_version": 2, "log_id": "different"})
    ]
    before = [row[:] for row in sheet.rows]
    with pytest.raises(ValueError):
        gateway(sheet).append_changes(group())
    assert sheet.rows == before and sheet.requests == []


def test_grace_cancellation_and_quota_failure_preserve_pending(tmp_path):
    sheet = Sheets()
    factory = client(tmp_path, "a", group())
    with factory() as uow:
        uow.sync_for(TARGET).defer(("c1",), NOW + timedelta(seconds=30))
    assert publish(publisher(factory, sheet)) == 0
    assert sheet.requests == []
    with pytest.raises(sync.SyncJobCancelledError):
        publisher(factory, sheet).publish_once(
            cancelled=lambda: True, deadline=NOW + timedelta(minutes=2)
        )
    with factory() as uow:
        uow.sync_for(TARGET).defer(("c1",), NOW)
    sheet.fail = "reject"
    with pytest.raises(OSError):
        publish(publisher(factory, sheet))
    with factory() as uow:
        assert uow.sync_for(TARGET).pending() == group()
    assert len(sheet.rows) == 1


def test_conflicting_commit_envelope_is_durable_and_prevents_an_append(tmp_path):
    sheet = Sheets()
    sheet.rows.append([group("remote", group_id="g-c1")[0].canonical_json()])
    factory = client(tmp_path, "a", group())
    with pytest.raises(ValueError):
        publish(publisher(factory, sheet))
    assert sheet.requests == []
    with factory() as uow:
        assert uow.sync_for(TARGET).problems()
        assert uow.sync_for(TARGET).pending() == group()


def test_unverified_readback_retries_the_original_ids(tmp_path):
    sheet = Sheets()
    factory = client(tmp_path, "a", group())
    sheet.on_append = lambda: sheet.rows.pop()
    with pytest.raises(ValueError):
        publish(publisher(factory, sheet))
    with factory() as uow:
        assert uow.sync_for(TARGET).pending() == group()
    sheet.on_append = lambda: None
    assert publish(publisher(factory, sheet)) == 1
    raw = [
        request["body"]["requests"][0]["appendCells"]["rows"][0]["values"][0]["userEnteredValue"][
            "stringValue"
        ]
        for request in sheet.requests
    ]
    assert raw == [group()[0].canonical_json()] * 2


@pytest.mark.parametrize(
    "manifest",
    [
        '{"kind":"manifest","schema_version":99,"log_id":"log"}',
        '{"kind":"manifest","schema_version":2,"log_id":"old","log_id":"log"}',
    ],
)
def test_invalid_manifest_is_preserved_as_a_target_bound_problem(tmp_path, manifest):
    sheet = Sheets()
    sheet.rows[0] = [manifest]
    factory = client(tmp_path, "a", group())
    with pytest.raises(ValueError):
        publish(publisher(factory, sheet))
    with factory() as uow:
        assert uow.sync_for(TARGET).problems()
        assert uow.sync_for(TARGET).pending() == group()
    assert sheet.requests == []


def test_incomplete_observed_group_waits_and_can_complete_on_a_later_read(tmp_path):
    sheet = Sheets()
    complete = finalize_group((group()[0], replace(group("c2")[0], group_id="g-c1")))
    sheet.rows.append([complete[0].canonical_json()])
    factory = client(tmp_path, "a", group("local"))
    with pytest.raises(ValueError):
        publish(publisher(factory, sheet))
    with factory() as uow:
        assert complete[0] in uow.sync_for(TARGET).observed()
        assert uow.sync_for(TARGET).problems() == ()
        assert uow.sync_for(TARGET).get_state("incomplete_groups") == ("g-c1",)
    assert sheet.requests == []
    sheet.rows.append([complete[1].canonical_json()])
    assert publish(publisher(factory, sheet)) == 1


def test_expired_deadline_and_disabled_job_never_append(tmp_path):
    sheet = Sheets()
    factory = client(tmp_path, "a", group())
    with pytest.raises(TimeoutError):
        publisher(factory, sheet).publish_once(cancelled=lambda: False, deadline=NOW)
    with factory() as uow:
        uow.settings.save("google_sync_enabled", False, NOW)
    with pytest.raises(sync.SyncJobObsoleteError):
        publish(publisher(factory, sheet))
    assert sheet.requests == []


def test_adapter_refuses_partial_groups_and_snapshot_replacement():
    sheet = Sheets()
    complete = finalize_group((group()[0], replace(group("c2")[0], group_id="g-c1")))
    with pytest.raises(ValueError):
        gateway(sheet).append_changes(complete[:1])
    with pytest.raises(sync.GoogleSyncUpgradeRequiredError):
        gateway(sheet).replace_records([])
    assert sheet.requests == []
    assert len(sheet.rows) == 1


@pytest.mark.parametrize("count", [0, 2])
def test_missing_or_ambiguous_v2_tab_never_creates_or_replaces_one(monkeypatch, count):
    sheet = Sheets()
    original = sheet.get

    def get(**kwargs):
        if "range" in kwargs:
            return original(**kwargs)
        return Request(
            lambda: {
                "sheets": [
                    {"properties": {"sheetId": 17 + i, "title": "QI_FLOW_SYNC_V2"}}
                    for i in range(count)
                ]
            }
        )

    monkeypatch.setattr(sheet, "get", get)
    with pytest.raises(ValueError):
        gateway(sheet).append_changes(group())
    assert sheet.requests == []
