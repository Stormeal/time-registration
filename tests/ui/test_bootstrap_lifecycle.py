"""Bootstrap ownership must be established before any SQLite initialization."""

import sys
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from qi_flow import bootstrap
from qi_flow.infrastructure.backups import BackupManager, BackupView
from qi_flow.infrastructure.paths import AppPaths
from qi_flow.infrastructure.single_instance import SingleInstanceGuard
from qi_flow.infrastructure.sqlite.database import SQLiteDatabase


@pytest.mark.parametrize("ownership_error", [False, True])
def test_refused_ownership_never_initializes_database(
    qapp: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ownership_error: bool,
) -> None:
    data_dir = tmp_path / "new data directory"
    monkeypatch.setenv("QI_FLOW_DATA_DIR", str(data_dir))
    monkeypatch.setattr(bootstrap, "QApplication", lambda args: qapp)
    acquired_paths: list[Path | None] = []
    warnings: list[str] = []

    class Guard:
        def __init__(self, key: str, *, lock_path: Path | None = None) -> None:
            acquired_paths.append(lock_path)

        def try_acquire(self) -> bool:
            assert data_dir.is_dir()
            if ownership_error:
                raise OSError("Synthetic lock failure")
            return False

    def unexpected_runtime() -> None:
        pytest.fail("A process without ownership must never open SQLite")

    monkeypatch.setattr(bootstrap, "SingleInstanceGuard", Guard)
    monkeypatch.setattr(bootstrap, "build_runtime", unexpected_runtime)
    monkeypatch.setattr(
        bootstrap.QMessageBox, "critical", lambda parent, title, text: warnings.append(text)
    )

    assert bootstrap.run(["qi-flow"]) == (1 if ownership_error else 0)
    assert acquired_paths == [data_dir / "qi-flow.instance.lock"]
    assert bool(warnings) is ownership_error
    assert not (data_dir / "qi-flow.sqlite3").exists()


@pytest.mark.parametrize("launch_succeeds", [True, False])
def test_verified_recovery_releases_ownership_before_restart(
    qapp: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    launch_succeeds: bool,
) -> None:
    paths = AppPaths.for_root(tmp_path / "recovery data")
    paths.ensure()
    paths.database_file.write_bytes(b"unreadable synthetic database")
    backup_file = paths.backup_dir / "qi-flow-backup-2026-10-03.sqlite3"
    SQLiteDatabase(backup_file).initialize()
    monkeypatch.setenv("QI_FLOW_DATA_DIR", str(paths.data_dir))
    monkeypatch.setattr(bootstrap, "QApplication", lambda args: qapp)
    operations: list[str] = []
    guards: list[SingleInstanceGuard] = []
    warnings: list[str] = []

    class RecordingGuard(SingleInstanceGuard):
        def __init__(self, key: str, *, lock_path: Path | None = None) -> None:
            super().__init__(key, lock_path=lock_path)
            guards.append(self)

        def try_acquire(self) -> bool:
            acquired = super().try_acquire()
            operations.append("acquired")
            return acquired

        def release(self) -> None:
            super().release()
            operations.append("released")

    real_build_runtime = bootstrap.build_runtime
    real_valid_backups = BackupManager.valid_backups_in
    real_restore = BackupManager.replace_unreadable_database

    def open_unreadable_database() -> bootstrap.RuntimeContext:
        assert guards[0].is_primary
        operations.append("database-open")
        return real_build_runtime()

    def verify_backups(folder: Path) -> list[BackupView]:
        assert guards[0].is_primary
        backups = real_valid_backups(folder)
        assert len(backups) == 1
        assert backups[0].path == backup_file
        operations.append("backup-verified")
        return backups

    def confirm_restore(*args: object) -> QMessageBox.StandardButton:
        assert guards[0].is_primary
        operations.append("confirmed")
        return QMessageBox.StandardButton.Yes

    def restore_database(database_file: Path, backup: BackupView) -> None:
        assert guards[0].is_primary
        real_restore(database_file, backup)
        operations.append("restored")

    def launch_replacement(program: str, arguments: list[str]) -> tuple[bool, int]:
        assert program == sys.executable
        assert arguments == sys.argv[1:]
        operations.append("launch")
        if not launch_succeeds:
            return False, 0
        child = SingleInstanceGuard(
            "replacement", lock_path=paths.data_dir / "qi-flow.instance.lock"
        )
        guards.append(child)
        assert child.try_acquire(), "The restored child must acquire ownership immediately"
        SQLiteDatabase(paths.database_file).initialize()
        operations.append("child-opened-restored-database")
        return True, 123

    monkeypatch.setattr(bootstrap, "SingleInstanceGuard", RecordingGuard)
    monkeypatch.setattr(bootstrap, "build_runtime", open_unreadable_database)
    monkeypatch.setattr(BackupManager, "valid_backups_in", verify_backups)
    monkeypatch.setattr(BackupManager, "replace_unreadable_database", restore_database)
    monkeypatch.setattr(QMessageBox, "question", confirm_restore)
    monkeypatch.setattr(
        QMessageBox, "critical", lambda _parent, _title, text: warnings.append(text)
    )
    monkeypatch.setattr(bootstrap.QProcess, "startDetached", launch_replacement)

    try:
        assert bootstrap.run(["qi-flow"]) == (0 if launch_succeeds else 1), warnings
        assert operations[:7] == [
            "acquired",
            "database-open",
            "backup-verified",
            "confirmed",
            "restored",
            "released",
            "launch",
        ]
        assert not guards[0].is_primary
        if launch_succeeds:
            assert operations[7:] == ["child-opened-restored-database"]
            assert not warnings
        else:
            assert len(warnings) == 1
            assert "restored" in warnings[0].lower()
            assert "open QI Flow" in warnings[0]
            SQLiteDatabase(paths.database_file).initialize()
    finally:
        for guard in guards:
            guard.release()
