"""The on-disk diagnostic log excludes OAuth request and credential data."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import ProxyHandler, build_opener

import google_auth_oauthlib.flow  # type: ignore[import-untyped]
import pytest

from qi_flow import __version__
from qi_flow.infrastructure.google_oauth import GoogleOAuthStore
from qi_flow.infrastructure.logging import configure_logging


@pytest.fixture
def diagnostic_file(tmp_path: Path):  # type: ignore[no-untyped-def]
    root = logging.getLogger()
    previous_handlers = root.handlers[:]
    previous_level = root.level
    configure_logging(tmp_path)
    try:
        yield tmp_path / "qi-flow.log"
    finally:
        for handler in root.handlers[:]:
            root.removeHandler(handler)
            handler.close()
        for handler in previous_handlers:
            root.addHandler(handler)
        root.setLevel(previous_level)


def test_oauth_callback_never_reaches_diagnostic_file(
    monkeypatch: pytest.MonkeyPatch, diagnostic_file: Path
) -> None:
    """A real local OAuth callback must not persist its URL, code, or state."""
    code = "synthetic-auth-code-private"
    token = "synthetic-refresh-token-private"
    client = json.dumps(
        {
            "installed": {
                "client_id": "test.apps.googleusercontent.com",
                "client_secret": "synthetic-client-secret-private",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        }
    )
    monkeypatch.setattr(
        "qi_flow.infrastructure.google_oauth.keyring.get_password",
        lambda service, account: client,
    )

    callback_urls: list[str] = []
    callback_states: list[str] = []
    requests: list[threading.Thread] = []

    class LocalBrowser:
        def open(self, authorization_url: str, **_kwargs: object) -> bool:
            redirect_uri = parse_qs(urlsplit(authorization_url).query)["redirect_uri"][0]
            state = parse_qs(urlsplit(authorization_url).query)["state"][0]
            callback_states.append(state)
            callback_url = f"{redirect_uri}?{urlencode({'code': code, 'state': state})}"
            callback_urls.append(callback_url)

            def send_callback() -> None:
                with build_opener(ProxyHandler({})).open(
                    callback_url.replace("localhost", "127.0.0.1"), timeout=5
                ) as response:
                    response.read()

            request = threading.Thread(target=send_callback)
            requests.append(request)
            request.start()
            return True

    monkeypatch.setattr(google_auth_oauthlib.flow.webbrowser, "get", lambda browser: LocalBrowser())

    def fail_token_exchange(self: object, **_kwargs: object) -> None:
        raise ValueError(f"credential exchange failed: refresh_token={token}")

    monkeypatch.setattr(
        google_auth_oauthlib.flow.InstalledAppFlow, "fetch_token", fail_token_exchange
    )

    with pytest.raises(ValueError, match="Google authorization could not be completed"):
        GoogleOAuthStore().authorize()
    for request in requests:
        request.join(timeout=5)
        assert not request.is_alive()

    logging.getLogger("qi_flow.bootstrap").info(
        "QI Flow %s started; data directory initialized", __version__
    )
    for handler in logging.getLogger().handlers:
        handler.flush()
    content = diagnostic_file.read_text(encoding="utf-8")
    assert f"QI Flow {__version__} started" in content
    assert "Google authorization failed" in content
    for private_value in (
        code,
        *callback_states,
        token,
        callback_urls[0],
        "synthetic-client-secret-private",
    ):
        assert private_value not in content


def test_configuring_diagnostics_twice_keeps_one_file_handler(diagnostic_file: Path) -> None:
    configure_logging(diagnostic_file.parent)
    logging.getLogger("qi_flow.bootstrap").info("System tray unavailable; using window lifecycle")
    for handler in logging.getLogger().handlers:
        handler.flush()
    assert diagnostic_file.read_text(encoding="utf-8").count("System tray unavailable") == 1


def test_unapproved_application_message_and_traceback_stay_out_of_diagnostics(
    diagnostic_file: Path,
) -> None:
    secret = "synthetic-private-request-payload"
    logger = logging.getLogger("qi_flow.bootstrap")
    logger.warning("Unexpected request: %s", secret)
    try:
        raise ValueError(secret)
    except ValueError:
        logger.warning("System tray unavailable; using window lifecycle", exc_info=True)
    for handler in logging.getLogger().handlers:
        handler.flush()
    content = diagnostic_file.read_text(encoding="utf-8")
    assert "System tray unavailable; using window lifecycle" in content
    assert "Unexpected request" not in content
    assert secret not in content
