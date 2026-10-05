"""Per-machine Google desktop authorization, with tokens in Credential Manager only."""

from __future__ import annotations

import importlib
import json
import logging
import math
import socket
import webbrowser
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from time import monotonic
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

from qi_flow.application.sync_models import SyncAuthorizationRequiredError, SyncJobCancelledError

_SERVICE = "QI Flow Google Sheets Sync"
_TOKEN_ACCOUNT = "refresh-token"
_CLIENT_ACCOUNT = "desktop-client"
SCOPES = ("https://www.googleapis.com/auth/spreadsheets",)
_LOG = logging.getLogger(__name__)


class _CallbackServer(HTTPServer):
    allow_reuse_address = False
    expected_state = ""
    redirect_uri = ""
    callback_response: str | None = None
    declined = False

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def get_request(self) -> tuple[socket.socket, Any]:
        connection, address = super().get_request()
        connection.settimeout(0.2)  # A client that never sends headers cannot hang exit.
        return connection, address

    def handle_error(self, request: Any, client_address: Any) -> None:
        pass  # Raw request exceptions/URLs must not enter stderr or diagnostics.


class _CallbackHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        pass  # BaseHTTPRequestHandler normally logs the private callback query.

    def do_GET(self) -> None:
        server = cast(_CallbackServer, self.server)
        parts = urlsplit(self.path)
        query = parse_qs(parts.query)
        legitimate = parts.path == "/" and query.get("state") == [server.expected_state]
        code, error = query.get("code", []), query.get("error", [])
        if not legitimate or (len(code) != 1 and len(error) != 1):
            self.send_response(400)
            self.end_headers()
            return
        server.declined = bool(error)
        server.callback_response = server.redirect_uri + "?" + parts.query
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Authorization response received. Return to QI Flow to see the result.")


def _check_authorization(cancelled: Callable[[], bool], deadline: float) -> None:
    if cancelled():
        raise SyncJobCancelledError("Google authorization cancelled; existing access is unchanged.")
    if monotonic() >= deadline:
        raise TimeoutError("Google authorization timed out. Start authorization again when ready.")


def _authorization_error_category(error: Exception) -> str:
    if isinstance(error, KeyringError):
        return "CredentialManagerError"
    if isinstance(error, ImportError):
        return "DependencyError"
    if isinstance(error, OSError):
        return "NetworkError"
    return "ValueError"


class GoogleOAuthStore:
    def __init__(self, *, client_id: Callable[[], str | None] | None = None) -> None:
        self._client_id = client_id

    def _matching_client(self, token: str, setup: str | None) -> bool:
        try:
            stored_id = json.loads(setup or "{}")["installed"]["client_id"]
            token_id = json.loads(token)["client_id"]
            return (
                isinstance(stored_id, str)
                and token_id == stored_id
                and (self._client_id is None or self._client_id() == stored_id)
            )
        except (ValueError, TypeError, KeyError):
            return False

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

    def authorize(
        self, *, cancelled: Callable[[], bool] = lambda: False, timeout_seconds: float = 120.0
    ) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("Authorization needs a positive finite timeout.")
        deadline = monotonic() + timeout_seconds
        _check_authorization(cancelled, deadline)
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
            server = _CallbackServer(("127.0.0.1", 0), _CallbackHandler)
            try:
                server.redirect_uri = f"http://localhost:{server.server_port}/"
                flow.redirect_uri = server.redirect_uri
                authorization_url, server.expected_state = flow.authorization_url()
                _check_authorization(cancelled, deadline)
                if not webbrowser.get(None).open(authorization_url, new=1, autoraise=True):
                    raise OSError("The browser could not open the authorization page.")
                while server.callback_response is None:
                    _check_authorization(cancelled, deadline)
                    server.timeout = min(0.2, max(0.001, deadline - monotonic()))
                    server.handle_request()
                _check_authorization(cancelled, deadline)
                if server.declined:
                    raise ValueError("Google authorization was declined.")
                flow.fetch_token(
                    authorization_response=server.callback_response.replace(
                        "http://", "https://", 1
                    ),
                    timeout=min(15.0, max(0.001, deadline - monotonic())),
                )
                _check_authorization(cancelled, deadline)
                payload = flow.credentials.to_json()
                _check_authorization(cancelled, deadline)
                if keyring.get_password(_SERVICE, _CLIENT_ACCOUNT) != content or (
                    self._client_id is not None
                    and self._client_id() != json.loads(content)["installed"]["client_id"]
                ):
                    raise SyncJobCancelledError(
                        "Google client setup changed; authorize the current client again."
                    )
                keyring.set_password(_SERVICE, _TOKEN_ACCOUNT, payload)
            finally:
                server.server_close()
        except (SyncJobCancelledError, TimeoutError):
            raise
        except Exception as error:
            # OAuth values and callback URLs must never reach diagnostics.
            category = _authorization_error_category(error)
            _LOG.warning("Google authorization failed (%s)", category)
            raise ValueError(
                "Google authorization could not be completed "
                f"({category}). Restart QI Flow after installing Google sync support, "
                "then try again."
            ) from error

    def is_authorized(self) -> bool:
        try:
            token = keyring.get_password(_SERVICE, _TOKEN_ACCOUNT)
            return token is not None and self._matching_client(
                token, keyring.get_password(_SERVICE, _CLIENT_ACCOUNT)
            )
        except KeyringError:
            return False

    def credentials(self) -> Any:
        try:
            payload = keyring.get_password(_SERVICE, _TOKEN_ACCOUNT)
        except KeyringError as error:
            raise ValueError("Windows Credential Manager is unavailable.") from error
        if payload is None:
            raise SyncAuthorizationRequiredError("Authorize this machine before synchronizing.")
        if not self._matching_client(payload, keyring.get_password(_SERVICE, _CLIENT_ACCOUNT)):
            raise SyncAuthorizationRequiredError(
                "Google client setup changed; authorize this computer again."
            )
        try:
            credentials_module: Any = importlib.import_module("google.oauth2.credentials")
            request_module: Any = importlib.import_module("google.auth.transport.requests")
            credentials = credentials_module.Credentials.from_authorized_user_info(
                json.loads(payload)
            )
            if credentials.expired and credentials.refresh_token:
                request = request_module.Request()

                def bounded_request(*args: Any, **kwargs: Any) -> Any:
                    kwargs["timeout"] = 15.0
                    return request(*args, **kwargs)

                exceptions_module: Any = importlib.import_module("google.auth.exceptions")
                try:
                    credentials.refresh(bounded_request)
                except exceptions_module.RefreshError as error:
                    if error.retryable:
                        raise
                    raise SyncAuthorizationRequiredError(
                        "Google rejected this computer's saved sign-in. "
                        "Use Disconnect this computer, save your OAuth client setup again, "
                        "then use Authorize this computer before retrying. "
                        "Your saved hours are retained. Review the operation before retrying."
                    ) from error
                keyring.set_password(_SERVICE, _TOKEN_ACCOUNT, credentials.to_json())
            return credentials
        except SyncAuthorizationRequiredError:
            raise
        except (ImportError, KeyringError, ValueError) as error:
            raise SyncAuthorizationRequiredError(
                "Google authorization needs to be repeated."
            ) from error

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
