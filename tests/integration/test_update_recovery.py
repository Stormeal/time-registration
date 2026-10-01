from __future__ import annotations

import hashlib
import importlib
import json
import shutil
import zipfile
from pathlib import Path

import pytest


def runtime():
    return importlib.import_module("scripts.update_runtime")


def install(tmp_path: Path) -> Path:
    root = tmp_path / "Programs" / "QI Flow"
    current = root / "current"
    current.mkdir(parents=True)
    (current / "QI Flow.exe").write_bytes(b"old application")
    (root / "QI Flow Launcher.exe").write_bytes(b"stable launcher")
    (root / "unins000.exe").write_bytes(b"installer uninstaller")
    (root / "unins000.dat").write_bytes(b"installer record")
    return root


def package(tmp_path: Path, contents: bytes = b"new application") -> tuple[Path, str]:
    archive = tmp_path / "update.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("QI Flow/QI Flow.exe", contents)
    return archive, hashlib.sha256(archive.read_bytes()).hexdigest()


def test_successful_update_changes_only_replaceable_bundle(tmp_path: Path) -> None:
    root = install(tmp_path)
    data = tmp_path / "AppData" / "QI Flow"
    data.mkdir(parents=True)
    (data / "qi-flow.sqlite3").write_bytes(b"time records")
    archive, digest = package(tmp_path)

    runtime().apply_update(
        archive,
        root,
        digest,
        lambda executable, _token: executable.read_bytes() == b"new application",
    )

    assert (root / "current" / "QI Flow.exe").read_bytes() == b"new application"
    assert (root / "QI Flow Launcher.exe").read_bytes() == b"stable launcher"
    assert (root / "unins000.exe").read_bytes() == b"installer uninstaller"
    assert (root / "unins000.dat").read_bytes() == b"installer record"
    assert (data / "qi-flow.sqlite3").read_bytes() == b"time records"


def test_bad_digest_leaves_existing_bundle_usable(tmp_path: Path) -> None:
    root = install(tmp_path)
    archive, _digest = package(tmp_path)

    with pytest.raises(ValueError, match="integrity"):
        runtime().apply_update(archive, root, "0" * 64, lambda *_args: True)

    assert (root / "current" / "QI Flow.exe").read_bytes() == b"old application"


def test_unsafe_archive_path_is_rejected_before_swap(tmp_path: Path) -> None:
    root = install(tmp_path)
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("QI Flow/../../outside.exe", b"untrusted")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()

    with pytest.raises(ValueError, match="unsafe"):
        runtime().apply_update(archive, root, digest, lambda *_args: True)

    assert (root / "current" / "QI Flow.exe").read_bytes() == b"old application"
    assert not (tmp_path / "outside.exe").exists()


@pytest.mark.parametrize("crash_after", ["old_move", "new_move"])
def test_next_launch_restores_previous_bundle_after_interrupted_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, crash_after: str
) -> None:
    root = install(tmp_path)
    archive, digest = package(tmp_path)
    current = root / "current"
    original_replace = Path.replace

    def interrupt_after_replace(source: Path, target: Path) -> Path:
        result = original_replace(source, target)
        if (crash_after == "old_move" and source == current) or (
            crash_after == "new_move" and target == current and source != current
        ):
            raise KeyboardInterrupt("simulated process termination")
        return result

    monkeypatch.setattr(Path, "replace", interrupt_after_replace)
    with pytest.raises(KeyboardInterrupt):
        runtime().apply_update(archive, root, digest, lambda *_args: True)
    monkeypatch.setattr(Path, "replace", original_replace)

    assert runtime().recover_install(root) == "restored"
    assert (current / "QI Flow.exe").read_bytes() == b"old application"
    assert runtime().recover_install(root) == "unchanged"


def test_failed_trial_restores_previous_bundle(tmp_path: Path) -> None:
    root = install(tmp_path)
    archive, digest = package(tmp_path)

    with pytest.raises(RuntimeError, match=r"start|ready|validation"):
        runtime().apply_update(archive, root, digest, lambda *_args: False)

    assert (root / "current" / "QI Flow.exe").read_bytes() == b"old application"


def test_cleanup_failure_does_not_block_following_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = install(tmp_path)
    first, first_digest = package(tmp_path)
    original_rmtree = shutil.rmtree

    def blocked_cleanup(path: str | Path, *args, **kwargs) -> None:
        if "previous" in Path(path).name:
            raise PermissionError("simulated file lock")
        original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", blocked_cleanup)
    runtime().apply_update(first, root, first_digest, lambda *_args: True)
    monkeypatch.setattr(shutil, "rmtree", original_rmtree)
    second, second_digest = package(tmp_path, b"second application")

    runtime().apply_update(second, root, second_digest, lambda *_args: True)

    assert (root / "current" / "QI Flow.exe").read_bytes() == b"second application"
    assert (root / "unins000.exe").read_bytes() == b"installer uninstaller"


def test_malformed_transaction_cannot_escape_install_root(tmp_path: Path) -> None:
    root = install(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("untouched", encoding="utf-8")
    (root / "update-transaction.json").write_text(
        json.dumps(
            {
                "id": "bad",
                "phase": "swapping",
                "previous": "../../outside",
                "staging": "update-work/stage-bad",
                "sha256": "a" * 64,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"transaction|path"):
        runtime().recover_install(root)

    assert (outside / "keep.txt").read_text(encoding="utf-8") == "untouched"
    assert (root / "current" / "QI Flow.exe").read_bytes() == b"old application"
