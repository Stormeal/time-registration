"""Per-machine Google desktop authorization, with tokens in Credential Manager only."""

from __future__ import annotations

import importlib
import json
import logging
from typing import Any

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

_SERVICE = "QI Flow Google Sheets Sync"
_TOKEN_ACCOUNT = "refresh-token"
_CLIENT_ACCOUNT = "desktop-client"
SCOPES = ("https://www.googleapis.com/auth/spreadsheets",)
_LOG = logging.getLogger(__name__)


class GoogleOAuthStore:
    def save_client_json(self, content: str) -> str:
        try:
            client = json.loads(content)["installed"]
            client_id = client["client_id"]
            if not isinstance(client_id, str) or not client_id.endswith(
                ".apps.googleusercontent.com"
            ):
                raise ValueError
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError(
                "Choose the downloaded Google desktop OAuth client JSON file."
            ) from error
        try:
            keyring.set_password(_SERVICE, _CLIENT_ACCOUNT, content)
        except KeyringError as error:
            raise ValueError(
                "Windows Credential Manager could not save the Google client setup."
            ) from error
        return client_id

    def save_client(self, client_id: str, client_secret: str) -> None:
        if not client_id.endswith(".apps.googleusercontent.com") or not client_secret:
            raise ValueError("Enter the Google desktop client ID and newly created client secret.")
        content = json.dumps(
            {
                "installed": {
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": ["http://localhost"],
                }
            }
        )
        try:
            keyring.set_password(_SERVICE, _CLIENT_ACCOUNT, content)
        except KeyringError as error:
            raise ValueError(
                "Windows Credential Manager could not save the Google client setup."
            ) from error

    def authorize(self) -> None:
        try:
            content = keyring.get_password(_SERVICE, _CLIENT_ACCOUNT)
        except KeyringError as error:
            raise ValueError("Windows Credential Manager is unavailable.") from error
        if content is None:
            raise ValueError("Choose your downloaded Google OAuth client JSON file first.")
        try:
            flow_module: Any = importlib.import_module("google_auth_oauthlib.flow")
            flow = flow_module.InstalledAppFlow.from_client_config(
                json.loads(content), list(SCOPES)
            )
            credentials = flow.run_local_server(port=0, open_browser=True)
            keyring.set_password(_SERVICE, _TOKEN_ACCOUNT, credentials.to_json())
        except (ImportError, KeyringError, OSError, ValueError) as error:
            # OAuth values and callback URLs must never reach diagnostics.
            _LOG.warning("Google authorization failed (%s)", type(error).__name__)
            raise ValueError(
                "Google authorization could not be completed "
                f"({type(error).__name__}). Restart QI Flow after installing Google sync support, "
                "then try again."
            ) from error

    def is_authorized(self) -> bool:
        try:
            return keyring.get_password(_SERVICE, _TOKEN_ACCOUNT) is not None
        except KeyringError:
            return False

    def credentials(self) -> Any:
        try:
            payload = keyring.get_password(_SERVICE, _TOKEN_ACCOUNT)
        except KeyringError as error:
            raise ValueError("Windows Credential Manager is unavailable.") from error
        if payload is None:
            raise ValueError("Authorize this machine before synchronizing.")
        try:
            credentials_module: Any = importlib.import_module("google.oauth2.credentials")
            request_module: Any = importlib.import_module("google.auth.transport.requests")
            credentials = credentials_module.Credentials.from_authorized_user_info(
                json.loads(payload)
            )
            if credentials.expired and credentials.refresh_token:
                credentials.refresh(request_module.Request())
                keyring.set_password(_SERVICE, _TOKEN_ACCOUNT, credentials.to_json())
            return credentials
        except (ImportError, KeyringError, ValueError) as error:
            raise ValueError("Google authorization needs to be repeated.") from error

    def disconnect(self) -> None:
        for account in (_TOKEN_ACCOUNT, _CLIENT_ACCOUNT):
            try:
                keyring.delete_password(_SERVICE, account)
            except PasswordDeleteError:
                continue
            except KeyringError as error:
                raise ValueError(
                    "Windows Credential Manager could not remove Google sync access."
                ) from error
