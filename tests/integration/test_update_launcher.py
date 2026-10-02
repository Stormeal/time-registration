from __future__ import annotations

import hashlib
import importlib
import subprocess
import zipfile
from pathlib import Path
from threading import Event, Thread

import pytest
from scripts.update_runtime import apply_update, install_lock


def launcher():
    return importlib.import_module("scripts.qi_flow_launcher")


def installation(tmp_path: Path) -> Path:
    root = tmp_path / "Programs" / "QI Flow"
    current = root / "current"
    current.mkdir(parents=True)
    (current / "QI Flow.exe").write_bytes(b"old")
    (root / "QI Flow Launcher.exe").write_bytes(b"stable launcher")
    return root


def update_archive(tmp_path: Path) -> tuple[Path, str]:
    archive = tmp_path / "update.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("QI Flow/QI Flow.exe", b"new")
    return archive, hashlib.sha256(archive.read_bytes()).hexdigest()


def test_normal_launch_recovers_an_interrupted_swap(tmp_path: Path, monkeypatch) -> None:
    root = installation(tmp_path)
    archive, digest = update_archive(tmp_path)
    current = root / "current"
    original_replace = Path.replace

    def stop_after_old_move(source: Path, target: Path) -> Path:
        result = original_replace(source, target)
        if source == current:
            raise KeyboardInterrupt("simulated helper termination")
        return result

    monkeypatch.setattr(Path, "replace", stop_after_old_move)
    with pytest.raises(KeyboardInterrupt):
        apply_update(archive, root, digest, lambda *_args: True)
    monkeypatch.setattr(Path, "replace", original_replace)
    started: list[tuple[list[str], Path]] = []

    launcher().launch(
        root, ["--start-minimized"], lambda command, cwd: started.append((command, cwd))
    )

    assert (current / "QI Flow.exe").read_bytes() == b"old"
    assert started == [([str(current / "QI Flow.exe"), "--start-minimized"], current)]


def test_normal_launch_waits_for_active_update_lock(tmp_path: Path) -> None:
    root = installation(tmp_path)
    locked = Event()
    release = Event()
    started = Event()

    def hold_lock() -> None:
        with install_lock(root):
            locked.set()
            assert release.wait(3)

    first = Thread(target=hold_lock)
    second = Thread(target=lambda: launcher().launch(root, [], lambda *_args: started.set()))
    first.start()
    try:
        assert locked.wait(3)
        second.start()
        assert not started.wait(0.1)
        release.set()
        assert started.wait(3)
    finally:
        release.set()
        first.join(3)
        if second.ident is not None:
            second.join(3)


class LivingProcess:
    def __init__(self) -> None:
        self.terminated = False

    def poll(self) -> None:
        return None

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        assert timeout is not None
        return 0


def test_trial_accepts_only_matching_readiness_marker(tmp_path: Path) -> None:
    root = installation(tmp_path)
    executable = root / "current" / "QI Flow.exe"
    token = "a" * 32
    process = LivingProcess()

    def start(command: list[str], cwd: Path) -> LivingProcess:
        assert command == [str(executable), "--update-ready", token]
        assert cwd == executable.parent
        marker = root / "update-work" / f"ready-{token}"
        marker.parent.mkdir(exist_ok=True)
        marker.write_text(token, encoding="ascii")
        return process

    assert launcher().trial_launch(executable, token, start, timeout_seconds=0.1)
    assert not process.terminated


def test_trial_rejects_mismatched_readiness_marker(tmp_path: Path) -> None:
    root = installation(tmp_path)
    executable = root / "current" / "QI Flow.exe"
    token = "b" * 32
    process = LivingProcess()

    def start(_command: list[str], _cwd: Path) -> LivingProcess:
        marker = root / "update-work" / f"ready-{token}"
        marker.parent.mkdir(exist_ok=True)
        marker.write_text("wrong token", encoding="ascii")
        return process

    assert not launcher().trial_launch(executable, token, start, timeout_seconds=0.1)
    assert process.terminated


def test_trial_rejects_process_that_exits_just_after_readiness(tmp_path: Path) -> None:
    root = installation(tmp_path)
    executable = root / "current" / "QI Flow.exe"
    token = "e" * 32

    class ExitingProcess(LivingProcess):
        def __init__(self) -> None:
            super().__init__()
            self.polls = 0

        def poll(self) -> int | None:
            self.polls += 1
            return 1 if self.polls >= 3 else None

    process = ExitingProcess()

    def start(_command: list[str], _cwd: Path) -> ExitingProcess:
        marker = root / "update-work" / f"ready-{token}"
        marker.parent.mkdir(exist_ok=True)
        marker.write_text(token, encoding="ascii")
        return process

    assert not launcher().trial_launch(executable, token, start, timeout_seconds=0.2)


def test_trial_kills_a_process_that_ignores_termination(tmp_path: Path) -> None:
    root = installation(tmp_path)
    executable = root / "current" / "QI Flow.exe"
    token = "9" * 32

    class StubbornProcess(LivingProcess):
        def __init__(self) -> None:
            super().__init__()
            self.killed = False

        def poll(self) -> int | None:
            return 1 if self.killed else None

        def wait(self, timeout: float | None = None) -> int:
            if not self.killed:
                raise subprocess.TimeoutExpired(str(executable), timeout)
            return 1

        def kill(self) -> None:
            self.killed = True

    process = StubbornProcess()

    assert not launcher().trial_launch(
        executable, token, lambda *_args: process, timeout_seconds=0.1
    )
    assert process.terminated
    assert process.killed


def test_app_rejects_an_invalid_readiness_token() -> None:
    from qi_flow import bootstrap

    with pytest.raises(ValueError, match="readiness token"):
        bootstrap.extract_update_ready_args(["--update-ready", "../../outside"])


def test_app_removes_valid_readiness_arguments_before_qt() -> None:
    from qi_flow import bootstrap

    token = "c" * 32
    assert bootstrap.extract_update_ready_args(["--start-minimized", "--update-ready", token]) == (
        ["--start-minimized"],
        token,
    )


def test_database_restore_does_not_restart_a_trial_app(monkeypatch: pytest.MonkeyPatch) -> None:
    from qi_flow import bootstrap

    started: list[object] = []
    monkeypatch.setattr(bootstrap.QProcess, "startDetached", lambda *args: started.append(args))

    bootstrap._restart_after_restore("a" * 32)

    assert started == []


def test_database_restore_restarts_through_stable_launcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from qi_flow import bootstrap

    root = installation(tmp_path)
    executable = root / "current" / "QI Flow.exe"
    started: list[tuple[object, ...]] = []
    monkeypatch.setattr(bootstrap.sys, "frozen", True, raising=False)
    monkeypatch.setattr(bootstrap.sys, "executable", str(executable))
    monkeypatch.setattr(bootstrap.sys, "argv", [str(executable), "--start-minimized"])
    monkeypatch.setattr(bootstrap.QProcess, "startDetached", lambda *args: started.append(args))

    bootstrap._restart_after_restore(None)

    assert started == [(str(root / "QI Flow Launcher.exe"), ["--start-minimized"], str(root))]


def test_app_signals_readiness_in_custom_installer_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from qi_flow import bootstrap

    root = installation(tmp_path).rename(tmp_path / "Programs" / "Custom QI Folder")
    monkeypatch.setattr(bootstrap.sys, "frozen", True, raising=False)
    monkeypatch.setattr(bootstrap.sys, "executable", str(root / "current" / "QI Flow.exe"))
    token = "f" * 32

    bootstrap._signal_update_ready(token)

    assert (root / "update-work" / f"ready-{token}").read_text(encoding="ascii") == token
