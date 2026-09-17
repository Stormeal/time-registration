"""Explicit opt-in editor for an optional Windows Credential Manager sign-in."""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QMessageBox

from qi_flow.application.testhuset import TesthusetCredential, TesthusetCredentialStore


class TesthusetCredentialsDialog(QDialog):
    __test__ = False

    def __init__(self, store: TesthusetCredentialStore) -> None:
        super().__init__()
        self.setWindowTitle("Save Testhuset sign-in")
        self._store = store
        self._username = QLineEdit()
        self._password = QLineEdit()
        self._password.setEchoMode(QLineEdit.EchoMode.Password)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout = QFormLayout(self)
        description = QLabel(
            "This saves an optional sign-in only in Windows Credential Manager for this Windows "
            "user. QI Flow does not add it to its files, backups, exports, or logs."
        )
        description.setWordWrap(True)
        layout.addRow(description)
        layout.addRow("Username", self._username)
        layout.addRow("Password", self._password)
        layout.addRow(buttons)

    def _save(self) -> None:
        try:
            self._store.save(TesthusetCredential(self._username.text(), self._password.text()))
        except ValueError as error:
            QMessageBox.warning(self, "Could not save Testhuset sign-in", str(error))
            return
        self.accept()
