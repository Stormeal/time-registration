"""Configuration boundary for optional Google Sheets synchronization."""

from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse

from qi_flow.application.ports import Clock, UnitOfWork


@dataclass(frozen=True, slots=True)
class GoogleSyncConfiguration:
    sheet_url: str
    oauth_client_id: str

    def __post_init__(self) -> None:
        parsed = urlparse(self.sheet_url)
        if (
            parsed.scheme != "https"
            or parsed.netloc != "docs.google.com"
            or "/spreadsheets/d/" not in parsed.path
        ):
            raise ValueError("Enter a private Google Sheets URL.")
        if not self.oauth_client_id.endswith(".apps.googleusercontent.com"):
            raise ValueError("Enter a Google desktop OAuth client ID.")


class GoogleSyncSettings:
    def __init__(self, uow_factory: Callable[[], UnitOfWork], clock: Clock) -> None:
        self._uow_factory, self._clock = uow_factory, clock

    def load(self) -> GoogleSyncConfiguration | None:
        with self._uow_factory() as uow:
            sheet_url = uow.settings.get("google_sync_sheet_url")
            client_id = uow.settings.get("google_sync_client_id")
        if not isinstance(sheet_url, str) or not isinstance(client_id, str):
            return None
        return GoogleSyncConfiguration(sheet_url, client_id)

    def save(self, configuration: GoogleSyncConfiguration) -> GoogleSyncConfiguration:
        with self._uow_factory() as uow:
            now = self._clock.now()
            uow.settings.save("google_sync_sheet_url", configuration.sheet_url, now)
            uow.settings.save("google_sync_client_id", configuration.oauth_client_id, now)
        return configuration

    def save_values(self, sheet_url: str, oauth_client_id: str) -> GoogleSyncConfiguration:
        """Validate and persist the current settings form values as one connection."""
        return self.save(GoogleSyncConfiguration(sheet_url, oauth_client_id))
