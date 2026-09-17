from __future__ import annotations

import pytest

from qi_flow.application.testhuset import TesthusetCredential
from qi_flow.infrastructure import testhuset_credentials
from qi_flow.infrastructure.testhuset_credentials import WindowsCredentialStore


def test_windows_credential_store_keeps_credentials_out_of_application_files(monkeypatch) -> None:
    stored: dict[tuple[str, str], str] = {}
    monkeypatch.setattr(
        testhuset_credentials.keyring,
        "set_password",
        lambda service, account, value: stored.__setitem__((service, account), value),
    )
    monkeypatch.setattr(
        testhuset_credentials.keyring,
        "get_password",
        lambda service, account: stored.get((service, account)),
    )
    monkeypatch.setattr(
        testhuset_credentials.keyring,
        "delete_password",
        lambda service, account: stored.pop((service, account)),
    )
    store = WindowsCredentialStore()
    credential = TesthusetCredential("consultant@example.test", "secret")
    store.save(credential)
    assert store.load() == credential
    store.clear()
    assert store.load() is None


def test_missing_windows_backend_has_a_safe_actionable_error(monkeypatch) -> None:
    def missing_backend(service: str, account: str) -> None:
        raise ModuleNotFoundError("win32ctypes")

    monkeypatch.setattr(testhuset_credentials.keyring, "get_password", missing_backend)
    with pytest.raises(ValueError, match="Credential Manager"):
        WindowsCredentialStore().load()
