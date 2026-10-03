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


def test_normal_exit_cancels_owned_google_worker_before_event_loop_and_lock_release(
    qapp, tmp_path, monkeypatch
):
    import threading

    from PySide6.QtCore import QCoreApplication, QEvent, QTimer

    from qi_flow.ui.google_sync_controller import GoogleSyncController

    paths = AppPaths.for_root(tmp_path / "worker lifecycle")
    previous_quit_on_close = qapp.quitOnLastWindowClosed()
    monkeypatch.setenv("QI_FLOW_DATA_DIR", str(paths.data_dir))
    monkeypatch.setattr(bootstrap, "QApplication", lambda args: qapp)
    monkeypatch.setattr(bootstrap.QSystemTrayIcon, "isSystemTrayAvailable", lambda: False)
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    operations, controllers, windows = [], [], []
    started = threading.Event()
    backup_started = threading.Event()

    def copy_backup(self, *, cancelled, **kwargs):
        backup_started.set()
        while not cancelled():
            threading.Event().wait(0.01)
        operations.append("backup-cancelled")
        return None

    monkeypatch.setattr(bootstrap.BackupManager, "ensure_daily_backup", copy_backup)

    class OAuth:
        def __init__(self, **kwargs):
            pass

        def is_authorized(self):
            return False

        def authorize(self, *, cancelled, timeout_seconds):
            started.set()
            while not cancelled():
                threading.Event().wait(0.01)
            operations.append("cancelled")

    monkeypatch.setattr(bootstrap, "GoogleOAuthStore", OAuth)

    class Controller(GoogleSyncController):
        def __init__(self, parent):
            super().__init__(parent)
            controllers.append(self)
            self.ready_for_shutdown.connect(lambda: operations.append("ready"))

        def wait_for_shutdown(self):
            operations.append("after-event-loop")
            super().wait_for_shutdown()

    monkeypatch.setattr(bootstrap, "GoogleSyncController", Controller)
    real_window = bootstrap.MainWindow

    def window(*args, **kwargs):
        result = real_window(*args, **kwargs)
        windows.append(result)
        return result

    monkeypatch.setattr(bootstrap, "MainWindow", window)

    class Guard(SingleInstanceGuard):
        def release(self):
            if controllers:
                assert not controllers[0].busy
                operations.append("released")
            super().release()

    monkeypatch.setattr(bootstrap, "SingleInstanceGuard", Guard)
    real_exec = qapp.exec

    def run_loop():
        windows[0]._settings_page._authorize_google()

        def close_when_started():
            if not started.is_set() or not backup_started.is_set():
                QTimer.singleShot(5, close_when_started)
                return
            operations.append("close-requested")
            windows[0].close()

        QTimer.singleShot(0, close_when_started)
        return real_exec()

    monkeypatch.setattr(qapp, "exec", run_loop)
    assert bootstrap.run(["qi-flow"]) == 0
    expected = ("close-requested", "cancelled", "ready", "after-event-loop", "released")
    assert [item for item in operations if item != "backup-cancelled"][:5] == list(expected)
    assert operations.index("backup-cancelled") < operations.index("after-event-loop")
    qapp.setQuitOnLastWindowClosed(previous_quit_on_close)
    QCoreApplication.removePostedEvents(qapp, QEvent.Type.Quit)
    for window in windows:
        window.set_tray_available(True)
        window.hide()
        window.deleteLater()
    for controller in controllers:
        controller.deleteLater()


def test_reviewed_restore_waits_for_workers_and_launches_only_after_lock_release(
    qapp, tmp_path, monkeypatch
):
    from datetime import datetime

    from PySide6.QtCore import QCoreApplication, QEvent, QTimer

    from qi_flow.application.backups import BackupView
    from qi_flow.domain.time_rules import COPENHAGEN

    paths = AppPaths.for_root(tmp_path / "reviewed restore")
    monkeypatch.setenv("QI_FLOW_DATA_DIR", str(paths.data_dir))
    previous_quit = qapp.quitOnLastWindowClosed()
    monkeypatch.setattr(bootstrap, "QApplication", lambda args: qapp)
    monkeypatch.setattr(bootstrap.QSystemTrayIcon, "isSystemTrayAvailable", lambda: False)
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    events, windows = [], []
    real_window = bootstrap.MainWindow

    def window(*args, **kwargs):
        result = real_window(*args, **kwargs)
        windows.append(result)
        return result

    monkeypatch.setattr(bootstrap, "MainWindow", window)
    monkeypatch.setattr(
        bootstrap.BackupManager, "restore", lambda self, backup: events.append("restore")
    )

    class Guard(SingleInstanceGuard):
        def release(self):
            events.append("release")
            super().release()

    monkeypatch.setattr(bootstrap, "SingleInstanceGuard", Guard)

    def launch(*args):
        assert events == ["restore", "release"]
        replacement = SingleInstanceGuard(
            bootstrap._guard_key(paths.data_dir), lock_path=paths.data_dir / "qi-flow.instance.lock"
        )
        assert replacement.try_acquire()
        replacement.release()
        events.append("launch")
        return True, 12345

    monkeypatch.setattr(bootstrap.QProcess, "startDetached", launch)
    real_exec = qapp.exec

    def run_loop():
        backup = BackupView(
            tmp_path / "synthetic.sqlite3", datetime(2026, 10, 3, tzinfo=COPENHAGEN)
        )
        QTimer.singleShot(0, lambda: windows[0].restore_requested.emit(backup))
        watchdog = QTimer(qapp)
        watchdog.setSingleShot(True)
        watchdog.timeout.connect(qapp.quit)
        watchdog.start(5000)
        result = real_exec()
        watchdog.stop()
        return result

    monkeypatch.setattr(qapp, "exec", run_loop)
    assert bootstrap.run(["qi-flow"]) == 0
    assert events == ["restore", "release", "launch"]
    qapp.setQuitOnLastWindowClosed(previous_quit)
    QCoreApplication.removePostedEvents(qapp, QEvent.Type.Quit)
    for window in windows:
        window.hide()
        window.deleteLater()
