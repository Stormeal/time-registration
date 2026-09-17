"""Opt-in Testhuset credentials protected by Windows Credential Manager."""

from __future__ import annotations

import json

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

from qi_flow.application.testhuset import TesthusetCredential

_SERVICE = "QI Flow Testhuset"
_ACCOUNT = "sign-in"


class WindowsCredentialStore:
    """Keep an optional credential in the current Windows user's credential vault only."""

    def load(self) -> TesthusetCredential | None:
        try:
            payload = keyring.get_password(_SERVICE, _ACCOUNT)
        except (ImportError, KeyringError) as error:
            raise ValueError(
                "Windows Credential Manager is unavailable. Sign in directly instead."
            ) from error
        if payload is None:
            return None
        try:
            username, password = json.loads(payload)
        except (TypeError, ValueError) as error:
            raise ValueError(
                "The saved Testhuset sign-in is invalid. Save it again in Settings."
            ) from error
        if (
            not isinstance(username, str)
            or not username
            or not isinstance(password, str)
            or not password
        ):
            raise ValueError("The saved Testhuset sign-in is invalid. Save it again in Settings.")
        return TesthusetCredential(username, password)

    def save(self, credential: TesthusetCredential) -> None:
        if not credential.username.strip() or not credential.password:
            raise ValueError("Enter both a Testhuset username and password.")
        try:
            keyring.set_password(
                _SERVICE, _ACCOUNT, json.dumps((credential.username, credential.password))
            )
        except (ImportError, KeyringError) as error:
            raise ValueError("Windows Credential Manager could not save this sign-in.") from error

    def clear(self) -> None:
        try:
            keyring.delete_password(_SERVICE, _ACCOUNT)
        except PasswordDeleteError:
            return
        except (ImportError, KeyringError) as error:
            raise ValueError("Windows Credential Manager could not remove this sign-in.") from error
