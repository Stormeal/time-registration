"""Google Sheets adapter that owns only the QI Flow structured sync tab."""

from __future__ import annotations

import importlib
import json
from typing import Any
from urllib.parse import urlparse

from qi_flow.application.google_sync import GoogleSyncConfiguration
from qi_flow.infrastructure.google_oauth import GoogleOAuthStore

_TAB = "QI_FLOW_SYNC_V1"


class GoogleSheetsSync:
    def __init__(self, configuration: GoogleSyncConfiguration, oauth: GoogleOAuthStore) -> None:
        self._configuration, self._oauth = configuration, oauth

    def replace_records(self, records: list[dict[str, Any]]) -> int:
        spreadsheet_id = urlparse(self._configuration.sheet_url).path.split("/d/")[1].split("/")[0]
        discovery: Any = importlib.import_module("googleapiclient.discovery")
        service = discovery.build("sheets", "v4", credentials=self._oauth.credentials())
        metadata = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        sheets = metadata.get("sheets", [])
        matching = [item for item in sheets if item.get("properties", {}).get("title") == _TAB]
        if not matching:
            service.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"requests": [{"addSheet": {"properties": {"title": _TAB}}}]},
            ).execute()
        elif matching[0].get("properties", {}).get("hidden"):
            service.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={
                    "requests": [
                        {
                            "updateSheetProperties": {
                                "properties": {
                                    "sheetId": matching[0]["properties"]["sheetId"],
                                    "hidden": False,
                                },
                                "fields": "hidden",
                            }
                        }
                    ]
                },
            ).execute()
        values = [["kind", "id", "revision", "updated_at_utc", "payload_json"]]
        values.extend(
            [
                item["kind"],
                item["id"],
                item["revision"],
                item["updated_at_utc"],
                json.dumps(item["payload"], sort_keys=True),
            ]
            for item in records
        )
        api = service.spreadsheets().values()
        api.clear(spreadsheetId=spreadsheet_id, range=f"{_TAB}!A:Z", body={}).execute()
        api.update(
            spreadsheetId=spreadsheet_id,
            range=f"{_TAB}!A1",
            valueInputOption="RAW",
            body={"values": values},
        ).execute()
        return len(records)

    def read_records(self) -> list[dict[str, Any]]:
        spreadsheet_id = urlparse(self._configuration.sheet_url).path.split("/d/")[1].split("/")[0]
        discovery: Any = importlib.import_module("googleapiclient.discovery")
        service = discovery.build("sheets", "v4", credentials=self._oauth.credentials())
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
