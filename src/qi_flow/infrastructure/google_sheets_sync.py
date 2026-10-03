"""Google Sheets adapter that owns only the QI Flow structured sync tab."""

from __future__ import annotations

import hashlib
import importlib
import json
from collections.abc import Callable, Sequence
from typing import Any
from urllib.parse import urlparse

from qi_flow.application.google_sync import GoogleSyncConfiguration
from qi_flow.application.google_sync_service import GoogleSyncUpgradeRequiredError
from qi_flow.application.sync_models import (
    SyncChange,
    SyncProblem,
    SyncTarget,
    canonical_json,
    parse_json,
    validate_group,
)
from qi_flow.infrastructure.google_oauth import GoogleOAuthStore

_TAB = "QI_FLOW_SYNC_V1"
_V2_TAB = "QI_FLOW_SYNC_V2"


class GoogleSheetsSync:
    def __init__(
        self,
        configuration: GoogleSyncConfiguration,
        oauth: GoogleOAuthStore | None,
        *,
        target: SyncTarget | None = None,
        service_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._configuration, self._oauth = configuration, oauth
        if target is not None and target.spreadsheet_id != configuration.spreadsheet_id:
            raise ValueError("The sync target belongs to a different spreadsheet.")
        self._target = target
        self._service_factory = service_factory
        self._problems: tuple[SyncProblem, ...] = ()

    def _service(self) -> Any:
        if self._service_factory is not None:
            return self._service_factory()
        if self._oauth is None:
            raise ValueError("Google authorization is required.")
        discovery: Any = importlib.import_module("googleapiclient.discovery")
        return discovery.build("sheets", "v4", credentials=self._oauth.credentials())

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

    def append_changes(self, changes: Sequence[SyncChange]) -> None:
        groups: dict[str, list[SyncChange]] = {}
        for change in changes:
            groups.setdefault(change.group_id, []).append(change)
        for group in groups.values():
            validate_group(group)
        if not changes:
            return
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
