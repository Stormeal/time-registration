"""Google Sheets connection settings tests without UI or Google dependencies."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from qi_flow.application.google_sync import GoogleSyncSettings
from qi_flow.application.ports import UnitOfWork as UnitOfWorkPort


class Clock:
    def now(self) -> datetime:
        return datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


class Settings:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    def get(self, key: str) -> Any | None:
        return self.values.get(key)

    def save(self, key: str, value: Any, updated_at: datetime) -> None:
        self.values[key] = value


class UnitOfWork:
    def __init__(self) -> None:
        self.settings = Settings()

    def __enter__(self) -> UnitOfWork:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def test_save_values_persists_connection_for_immediate_sync() -> None:
    unit_of_work = UnitOfWork()
    settings = GoogleSyncSettings(lambda: cast(UnitOfWorkPort, unit_of_work), Clock())

    saved = settings.save_values(
        "https://docs.google.com/spreadsheets/d/example-sheet-id/edit",
        "desktop-client.apps.googleusercontent.com",
    )

    assert settings.load() == saved
