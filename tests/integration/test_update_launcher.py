from __future__ import annotations

import hashlib
import importlib
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
