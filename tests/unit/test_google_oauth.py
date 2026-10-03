"""Bounded loopback authorization never commits a partial or cancelled token."""

import json
import socket
import threading
from time import monotonic
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import ProxyHandler, build_opener

import pytest

from qi_flow.application.sync_models import SyncJobCancelledError
from qi_flow.infrastructure.google_oauth import GoogleOAuthStore

CLIENT = json.dumps(
    {
        "installed": {
            "client_id": "test.apps.googleusercontent.com",
            "client_secret": "synthetic",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }
)


@pytest.fixture
def rig(monkeypatch):
    import google_auth_oauthlib.flow

    values = {"desktop-client": CLIENT, "refresh-token": "previous-valid-token"}
    monkeypatch.setattr(
        "qi_flow.infrastructure.google_oauth.keyring.get_password",
        lambda service, account: values.get(account),
    )
    monkeypatch.setattr(
        "qi_flow.infrastructure.google_oauth.keyring.set_password",
        lambda service, account, payload: values.__setitem__(account, payload),
    )

    class Credentials:
        def to_json(self):
            return "new-valid-token"

    class Flow:
        credentials = Credentials()

        def authorization_url(self):
            return "https://example.invalid/auth?" + urlencode(
                {"redirect_uri": self.redirect_uri, "state": "known-state"}
            ), "known-state"

        def fetch_token(self, **kwargs):
            self.exchange = kwargs

    flow = Flow()
    monkeypatch.setattr(
        google_auth_oauthlib.flow.InstalledAppFlow, "from_client_config", lambda *args: flow
    )
    urls, threads = [], []

    class Browser:
        def open(self, url, **kwargs):
            urls.append(url)
            return True

    browser = Browser()
    monkeypatch.setattr(google_auth_oauthlib.flow.webbrowser, "get", lambda *args: browser)

    def callback(error=None, state="known-state"):
        def open_url(url, **kwargs):
            urls.append(url)
            redirect = parse_qs(urlsplit(url).query)["redirect_uri"][0].replace(
                "localhost", "127.0.0.1"
            )
            query = (
                {"state": state, "error": error}
                if error
                else {"state": state, "code": "synthetic-private-code"}
            )

            def request():
                try:
                    with build_opener(ProxyHandler({})).open(
                        redirect + "?" + urlencode(query), timeout=2
                    ) as response:
                        response.read()
                except HTTPError as error:
                    if error.code != 400:
                        raise

            thread = threading.Thread(target=request)
            threads.append(thread)
            thread.start()
            return True

        browser.open = open_url

    yield GoogleOAuthStore(), flow, values, urls, callback, browser
    for thread in threads:
        thread.join(timeout=3)
        assert not thread.is_alive()
    if urls:
        redirect = parse_qs(urlsplit(urls[0]).query)["redirect_uri"][0]
        port = urlsplit(redirect).port
        with pytest.raises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=0.1)


def test_abandoned_browser_has_deadline_releases_port_and_keeps_valid_token(rig):
    store, _, values, _, _, _ = rig
    started = monotonic()
    with pytest.raises(TimeoutError):
        store.authorize(cancelled=lambda: False, timeout_seconds=0.15)
    assert monotonic() - started < 2
    assert values["refresh-token"] == "previous-valid-token"


def test_cancellation_releases_callback_server_without_exchange_or_token_write(rig):
    store, flow, values, urls, _, _ = rig
    with pytest.raises(SyncJobCancelledError):
        store.authorize(cancelled=lambda: bool(urls), timeout_seconds=2)
    assert not hasattr(flow, "exchange")
    assert values["refresh-token"] == "previous-valid-token"


def test_valid_callback_exchanges_with_http_timeout_and_commits_only_complete_token(rig):
    store, flow, values, _, callback, _ = rig
    callback()
    store.authorize(cancelled=lambda: False, timeout_seconds=2)
    assert values["refresh-token"] == "new-valid-token"
    assert 0 < flow.exchange["timeout"] <= 2
    assert flow.exchange["authorization_response"].startswith("https://localhost:")


def test_cancel_during_token_exchange_preserves_previous_credentials(rig):
    store, flow, values, _, callback, _ = rig
    callback()
    cancelled = threading.Event()
    flow.fetch_token = lambda **kwargs: cancelled.set()
    with pytest.raises(SyncJobCancelledError):
        store.authorize(cancelled=cancelled.is_set, timeout_seconds=2)
    assert values["refresh-token"] == "previous-valid-token"


def test_mismatched_state_is_not_exchanged_or_stored(rig):
    store, flow, values, _, callback, _ = rig
    callback(state="wrong-state")
    with pytest.raises(TimeoutError):
        store.authorize(cancelled=lambda: False, timeout_seconds=0.2)
    assert not hasattr(flow, "exchange")
    assert values["refresh-token"] == "previous-valid-token"


def test_browser_open_failure_does_not_wait_for_a_callback(rig):
    store, _, values, urls, _, browser = rig
    browser.open = lambda url, **kwargs: (urls.append(url), False)[1]
    with pytest.raises(ValueError, match="Google authorization could not be completed"):
        store.authorize(cancelled=lambda: False, timeout_seconds=2)
    assert values["refresh-token"] == "previous-valid-token"


def test_credential_store_failure_preserves_previous_token(rig, monkeypatch):
    from keyring.errors import KeyringError

    store, _, values, _, callback, _ = rig
    callback()

    def fail(*args):
        raise KeyringError("synthetic store failure")

    monkeypatch.setattr("qi_flow.infrastructure.google_oauth.keyring.set_password", fail)
    with pytest.raises(ValueError, match="CredentialManagerError"):
        store.authorize(cancelled=lambda: False, timeout_seconds=2)
    assert values["refresh-token"] == "previous-valid-token"


def test_refresh_uses_bounded_http_request(rig, monkeypatch):
    import google.auth.transport.requests
    import google.oauth2.credentials

    store, _, values, _, _, _ = rig
    values["refresh-token"] = "{}"
    observed = []

    class Request:
        def __call__(self, **kwargs):
            observed.append(kwargs)

    class Credentials:
        expired = True
        refresh_token = "synthetic"

        def refresh(self, request):
            request(url="https://example.invalid/token")

        def to_json(self):
            return "refreshed-token"

    monkeypatch.setattr(
        google.oauth2.credentials.Credentials,
        "from_authorized_user_info",
        lambda *args: Credentials(),
    )
    monkeypatch.setattr(google.auth.transport.requests, "Request", Request)
    store.credentials()
    assert 0 < observed[0]["timeout"] <= 15


def test_sheet_service_uses_bounded_authenticated_http(monkeypatch):
    import googleapiclient.discovery

    from qi_flow.application.google_sync import GoogleSyncConfiguration
    from qi_flow.infrastructure.google_sheets_sync import GoogleSheetsSync

    captured = []
    monkeypatch.setattr(
        googleapiclient.discovery, "build", lambda *args, **kwargs: captured.append(kwargs)
    )

    class OAuth:
        def credentials(self):
            return object()

    adapter = GoogleSheetsSync(
        GoogleSyncConfiguration(
            "https://docs.google.com/spreadsheets/d/sheet/edit", "test.apps.googleusercontent.com"
        ),
        OAuth(),
    )
    adapter._service()
    assert 0 < captured[0]["http"].http.timeout <= 15
