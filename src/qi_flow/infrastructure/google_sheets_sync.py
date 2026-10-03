"""Google Sheets adapter that owns only the QI Flow structured sync tab."""

from __future__ import annotations

import hashlib
import importlib
import json
import math
from collections.abc import Callable, Mapping, Sequence
from time import monotonic
from typing import Any
from urllib.parse import urlparse

from qi_flow.application.google_sync import GoogleSyncConfiguration
from qi_flow.application.google_sync_service import GoogleSyncUpgradeRequiredError
from qi_flow.application.sync_migration import fingerprint
from qi_flow.application.sync_models import (
    SyncChange,
    SyncJobCancelledError,
    SyncProblem,
    SyncTarget,
    canonical_json,
    parse_json,
    validate_group,
)
from qi_flow.infrastructure.google_oauth import GoogleOAuthStore

_TAB = "QI_FLOW_SYNC_V1"
_V2_TAB = "QI_FLOW_SYNC_V2"
_MIGRATION_TAB = "QI_FLOW_MIGRATION_V2"


class _GuardedApi:
    """Check around each execute, including multiple requests in one adapter method."""

    def __init__(self, api: Any, check: Callable[[], None]) -> None:
        self._api, self._check = api, check

    def __getattr__(self, name: str) -> Any:
        method = getattr(self._api, name)

        def call(*args: Any, **kwargs: Any) -> Any:
            self._check()
            result = method(*args, **kwargs)
            self._check()
            return result if name == "execute" else _GuardedApi(result, self._check)

        return call


class GoogleSheetsSync:
    def __init__(
        self,
        configuration: GoogleSyncConfiguration,
        oauth: GoogleOAuthStore | None,
        *,
        target: SyncTarget | None = None,
        service_factory: Callable[[], Any] | None = None,
        cancelled: Callable[[], bool] = lambda: False,
        timeout_seconds: float = 120.0,
    ) -> None:
        self._configuration, self._oauth = configuration, oauth
        if target is not None and target.spreadsheet_id != configuration.spreadsheet_id:
            raise ValueError("The sync target belongs to a different spreadsheet.")
        self._target = target
        self._service_factory = service_factory
        self._problems: tuple[SyncProblem, ...] = ()
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("Sync requests need a positive finite timeout.")
        self._cancelled, self._expires_at = cancelled, monotonic() + timeout_seconds

    def _check_request(self) -> None:
        if self._cancelled():
            raise SyncJobCancelledError("Sync cancelled; verify pending changes before retrying.")
        if monotonic() >= self._expires_at:
            raise TimeoutError("Sync request deadline expired; pending changes are retained.")

    def _service(self) -> Any:
        self._check_request()
        if self._service_factory is not None:
            return _GuardedApi(self._service_factory(), self._check_request)
        if self._oauth is None:
            raise ValueError("Google authorization is required.")
        discovery: Any = importlib.import_module("googleapiclient.discovery")
        httplib: Any = importlib.import_module("httplib2")
        authorized: Any = importlib.import_module("google_auth_httplib2")
        http = authorized.AuthorizedHttp(self._oauth.credentials(), http=httplib.Http(timeout=15))
        service = discovery.build("sheets", "v4", http=http, cache_discovery=False)
        self._check_request()
        return _GuardedApi(service, self._check_request)

    def _v2_rows(self, service: Any) -> tuple[int, list[list[object]]]:
        target = self._target
        if target is None:
            raise GoogleSyncUpgradeRequiredError(
                "Review initialization or migration before syncing V2."
            )
        metadata = service.spreadsheets().get(spreadsheetId=target.spreadsheet_id).execute()
        matching = [
            item["properties"]
            for item in metadata.get("sheets", [])
            if item.get("properties", {}).get("title") == _V2_TAB
        ]
        if len(matching) != 1:
            raise ValueError("The reviewed V2 sync tab is missing or ambiguous.")
        sheet_id = matching[0].get("sheetId")
        if type(sheet_id) is not int:
            raise ValueError("The V2 tab has an invalid identity.")
        rows = (
            service.spreadsheets()
            .values()
            .get(
                spreadsheetId=target.spreadsheet_id,
                range=f"{_V2_TAB}!A:A",
                valueRenderOption="FORMULA",
            )
            .execute()
            .get("values", [])
        )
        expected = {"kind": "manifest", "schema_version": 2, "log_id": target.log_id}
        first = rows[0] if rows else []
        try:
            valid = (
                len(first) == 1
                and isinstance(first[0], str)
                and canonical_json(parse_json(first[0])) == canonical_json(expected)
            )
        except (ValueError, TypeError, RecursionError):
            valid = False
        if not valid:
            source = f"{_V2_TAB}:manifest"
            identifier = hashlib.sha256(canonical_json(first).encode("utf-8")).hexdigest()
            self._problems = (SyncProblem(identifier, "invalid_log_manifest", first, source),)
            raise ValueError("The sync log identity changed; review this destination again.")
        return sheet_id, rows

    def read_changes(self) -> tuple[SyncChange, ...]:
        self._problems = ()
        self._guard_migration(())
        _, rows = self._v2_rows(self._service())
        changes: list[SyncChange] = []
        problems: list[SyncProblem] = []
        for index, row in enumerate(rows[1:], start=2):
            if not row or row == [""]:
                continue
            try:
                if len(row) != 1 or not isinstance(row[0], str):
                    raise ValueError("Invalid log row.")
                changes.append(SyncChange.from_json(row[0]))
            except (ValueError, TypeError, RecursionError):
                source = f"{_V2_TAB}:row:{index}"
                identifier = hashlib.sha256(
                    canonical_json({"row": row, "source": source}).encode("utf-8")
                ).hexdigest()
                problems.append(SyncProblem(identifier, "invalid_log_row", row, source))
        self._problems = tuple(problems)
        return tuple(changes)

    def read_problems(self) -> tuple[SyncProblem, ...]:
        return self._problems

    def read_legacy_rows(self) -> Sequence[Sequence[object]]:
        service = self._service()
        metadata = (
            service.spreadsheets().get(spreadsheetId=self._configuration.spreadsheet_id).execute()
        )
        matches = [
            s for s in metadata.get("sheets", []) if s.get("properties", {}).get("title") == _TAB
        ]
        if not matches:
            return ()
        if len(matches) != 1:
            raise ValueError("The original V1 tab is ambiguous; review it before migration.")
        rows: list[list[object]] = (
            service.spreadsheets()
            .values()
            .get(
                spreadsheetId=self._configuration.spreadsheet_id,
                range=f"{_TAB}!A:E",
                valueRenderOption="FORMULA",
            )
            .execute()
            .get("values", [])
        )
        return rows

    def read_migration_events(self) -> tuple[Mapping[str, object], ...]:
        service = self._service()
        metadata = (
            service.spreadsheets().get(spreadsheetId=self._configuration.spreadsheet_id).execute()
        )
        matches = [
            s
            for s in metadata.get("sheets", [])
            if s.get("properties", {}).get("title") == _MIGRATION_TAB
        ]
        if not matches:
            return ()
        if len(matches) != 1:
            raise ValueError("The migration ledger is ambiguous.")
        rows = (
            service.spreadsheets()
            .values()
            .get(
                spreadsheetId=self._configuration.spreadsheet_id,
                range=f"{_MIGRATION_TAB}!A:A",
                valueRenderOption="FORMULA",
            )
            .execute()
            .get("values", [])
        )
        events: list[Mapping[str, object]] = []
        for row in rows:
            if len(row) != 1 or not isinstance(row[0], str):
                raise ValueError("The migration safety ledger has an invalid row.")
            value = parse_json(row[0])
            if not isinstance(value, Mapping):
                raise ValueError("The migration safety ledger has an invalid record.")
            events.append(value)
        if not events or events[0].get("kind") != "migration_manifest":
            raise ValueError("The migration ledger has no reviewed manifest.")
        target = SyncTarget(
            str(events[0].get("spreadsheet_id", "")), str(events[0].get("log_id", ""))
        )
        if target.spreadsheet_id != self._configuration.spreadsheet_id or (
            self._target is not None and self._target != target
        ):
            raise ValueError("The migration log identity differs from this reviewed destination.")
        self._target = target
        self._v2_rows(service)
        return tuple(events)

    @staticmethod
    def _append_request(
        sheet_id: int, records: Sequence[Mapping[str, object]]
    ) -> dict[str, object]:
        return {
            "appendCells": {
                "sheetId": sheet_id,
                "fields": "userEnteredValue",
                "rows": [
                    {"values": [{"userEnteredValue": {"stringValue": canonical_json(record)}}]}
                    for record in records
                ],
            }
        }

    def initialize_migration(
        self, target: SyncTarget, events: Sequence[Mapping[str, object]]
    ) -> None:
        if (
            target.spreadsheet_id != self._configuration.spreadsheet_id
            or not events
            or events[0].get("log_id") != target.log_id
        ):
            raise ValueError("The migration manifest does not match the requested destination.")
        service = self._service()
        metadata = service.spreadsheets().get(spreadsheetId=target.spreadsheet_id).execute()
        titles = {s.get("properties", {}).get("title") for s in metadata.get("sheets", [])}
        if {_V2_TAB, _MIGRATION_TAB} & titles:
            raise ValueError("A V2 tab already exists; review and join its migration.")
        # Fixed IDs make this batch self-contained. A concurrent add/title collision
        # rejects the entire batch rather than overwriting a winning initializer.
        v2_id = int(fingerprint(target.log_id + ":changes")[:7], 16)
        ledger_id = int(fingerprint(target.log_id + ":migration")[:7], 16)
        if v2_id == ledger_id:
            raise ValueError("Migration tab identifiers collide; review initialization again.")
        requests = [
            {"addSheet": {"properties": {"sheetId": v2_id, "title": _V2_TAB}}},
            {"addSheet": {"properties": {"sheetId": ledger_id, "title": _MIGRATION_TAB}}},
            self._append_request(
                v2_id, ({"kind": "manifest", "schema_version": 2, "log_id": target.log_id},)
            ),
            self._append_request(ledger_id, events),
        ]
        service.spreadsheets().batchUpdate(
            spreadsheetId=target.spreadsheet_id, body={"requests": requests}
        ).execute()
        self._target = target
        self.read_migration_events()

    def append_migration_events(self, events: Sequence[Mapping[str, object]]) -> None:
        self.read_migration_events()
        if not events:
            return
        service = self._service()
        metadata = (
            service.spreadsheets().get(spreadsheetId=self._configuration.spreadsheet_id).execute()
        )
        matches = [
            s["properties"]
            for s in metadata.get("sheets", [])
            if s.get("properties", {}).get("title") == _MIGRATION_TAB
        ]
        if len(matches) != 1 or type(matches[0].get("sheetId")) is not int:
            raise ValueError("The migration ledger identity changed.")
        service.spreadsheets().batchUpdate(
            spreadsheetId=self._configuration.spreadsheet_id,
            body={"requests": [self._append_request(matches[0]["sheetId"], events)]},
        ).execute()

    def _guard_migration(self, changes: Sequence[SyncChange]) -> None:
        events = self.read_migration_events()
        if not events:
            return  # Pure transport fixtures; production activation requires migration.
        expected = events[0].get("legacy_fingerprint")
        raw = self.read_legacy_rows()
        if fingerprint(raw) != expected:
            self._problems = (SyncProblem(fingerprint(raw), "legacy_writes_resumed", raw, _TAB),)
            raise ValueError("V1 writes resumed; pause and upgrade old writers before review.")
        if not any(e.get("kind") == "cutover" for e in events) and any(
            not c.change_id.startswith("seed-") for c in changes
        ):
            raise ValueError("Only migration seeds may be appended before reviewed cutover.")

    def append_changes(self, changes: Sequence[SyncChange]) -> None:
        groups: dict[str, list[SyncChange]] = {}
        for change in changes:
            groups.setdefault(change.group_id, []).append(change)
        for group in groups.values():
            validate_group(group)
        if not changes:
            return
        self._guard_migration(changes)
        service = self._service()
        sheet_id, _ = self._v2_rows(service)
        assert self._target is not None
        service.spreadsheets().batchUpdate(
            spreadsheetId=self._target.spreadsheet_id,
            body={
                "requests": [
                    {
                        "appendCells": {
                            "sheetId": sheet_id,
                            "rows": [
                                {
                                    "values": [
                                        {
                                            "userEnteredValue": {
                                                "stringValue": change.canonical_json()
                                            }
                                        }
                                    ]
                                }
                                for change in changes
                            ],
                            "fields": "userEnteredValue",
                        }
                    }
                ]
            },
        ).execute()

    def replace_records(self, records: list[dict[str, Any]]) -> int:
        raise GoogleSyncUpgradeRequiredError(
            "Snapshot replacement is disabled; review V2 migration."
        )

    def read_records(self) -> list[dict[str, Any]]:
        spreadsheet_id = urlparse(self._configuration.sheet_url).path.split("/d/")[1].split("/")[0]
        service = self._service()
        metadata = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        titles = {item.get("properties", {}).get("title") for item in metadata.get("sheets", [])}
        if _TAB not in titles:
            return []
        values = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=f"{_TAB}!A:E")
            .execute()
            .get("values", [])
        )
        if not values:
            return []
        if values[0] != ["kind", "id", "revision", "updated_at_utc", "payload_json"]:
            raise ValueError("The QI Flow sync tab has an unexpected format.")
        records: list[dict[str, Any]] = []
        for row in values[1:]:
            if len(row) != 5:
                raise ValueError("The QI Flow sync tab contains an incomplete record.")
            try:
                records.append(
                    {
                        "kind": row[0],
                        "id": row[1],
                        "revision": int(row[2]),
                        "updated_at_utc": row[3],
                        "payload": json.loads(row[4]),
                    }
                )
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                raise ValueError("The QI Flow sync tab contains an invalid record.") from error
        return records
