from __future__ import annotations

import hashlib
import runpy
import shutil
import zipfile
from pathlib import Path

import pytest

_HELPER = runpy.run_path("scripts/update_helper.py")
_apply_update = _HELPER["apply_update"]
_main = _HELPER["main"]


def make_package(
    path: Path, *, member: str = "QI Flow/QI Flow.exe", contents: bytes = b"new"
) -> str:
    with zipfile.ZipFile(path, "w") as package:
        package.writestr(member, contents)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def add_uninstaller(install_dir: Path) -> None:
    (install_dir / "unins000.exe").write_bytes(b"uninstaller")
    (install_dir / "unins000.dat").write_bytes(b"install log")


def snapshot(directory: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(directory)): path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file()
    }


def run_main_without_windows_processes(
    monkeypatch, archive: Path, install_dir: Path, digest: str, *, launch_alive: bool = True
) -> int:
    class FakeProcess:
        def poll(self):
            return None if launch_alive else 1

        def terminate(self):
            pass

    monkeypatch.setitem(_main.__globals__, "_wait_for_process_exit", lambda pid: None)
    monkeypatch.setitem(_main.__globals__, "_wait_until_alive", lambda process: launch_alive)
    monkeypatch.setitem(_main.__globals__, "_show_error", lambda message: None)
    monkeypatch.setattr(
        _main.__globals__["subprocess"], "Popen", lambda *args, **kwargs: FakeProcess()
    )
    return _main(
        [
            "--pid",
            "42",
            "--archive",
            str(archive),
            "--install-dir",
            str(install_dir),
            "--sha256",
            digest,
        ]
    )


def test_main_preflight_refusal_preserves_existing_recovery(tmp_path, monkeypatch) -> None:
    install_dir = tmp_path / "Programs" / "QI Flow"
    recovery = install_dir.with_name("QI Flow.previous")
    install_dir.mkdir(parents=True)
    recovery.mkdir()
    (install_dir / "QI Flow.exe").write_bytes(b"current-good")
    add_uninstaller(install_dir)
    (recovery / "QI Flow.exe").write_bytes(b"older-release")
    (recovery / "unins000.dat").write_bytes(b"older-uninstall")
    before_install, before_recovery = snapshot(install_dir), snapshot(recovery)
    archive = tmp_path / "update.zip"
    digest = make_package(archive)

    assert run_main_without_windows_processes(monkeypatch, archive, install_dir, digest) == 1

    assert snapshot(install_dir) == before_install
    assert snapshot(recovery) == before_recovery


def test_main_relaunch_failure_restores_owned_recovery_and_preserves_existing_failed_tree(
    tmp_path, monkeypatch
) -> None:
    install_dir = tmp_path / "Programs" / "QI Flow"
    install_dir.mkdir(parents=True)
    (install_dir / "QI Flow.exe").write_bytes(b"current-good")
    add_uninstaller(install_dir)
    existing_failed = install_dir.with_name("QI Flow.failed")
    existing_failed.mkdir()
    (existing_failed / "QI Flow.exe").write_bytes(b"older-failed-attempt")
    data_dir = tmp_path / "AppData"
    data_dir.mkdir()
    (data_dir / "qi-flow.sqlite3").write_bytes(b"user data")
    archive = tmp_path / "update.zip"
    digest = make_package(archive)

    assert (
        run_main_without_windows_processes(
            monkeypatch, archive, install_dir, digest, launch_alive=False
        )
        == 1
    )

    assert (install_dir / "QI Flow.exe").read_bytes() == b"current-good"
    assert (install_dir / "unins000.dat").read_bytes() == b"install log"
    assert (existing_failed / "QI Flow.exe").read_bytes() == b"older-failed-attempt"
    assert (data_dir / "qi-flow.sqlite3").read_bytes() == b"user data"


def test_main_staging_cleanup_failure_keeps_new_install_and_recovery(tmp_path, monkeypatch) -> None:
    install_dir = tmp_path / "Programs" / "QI Flow"
    install_dir.mkdir(parents=True)
    (install_dir / "QI Flow.exe").write_bytes(b"current-good")
    add_uninstaller(install_dir)
    archive = tmp_path / "update.zip"
    digest = make_package(archive)
    original_rmtree = shutil.rmtree

    def fail_staging_cleanup(path: Path, *args, **kwargs) -> None:
        if Path(path).name.startswith(".qi-flow-update-"):
            raise OSError("staging cleanup failed")
        original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", fail_staging_cleanup)

    assert run_main_without_windows_processes(monkeypatch, archive, install_dir, digest) == 0

    assert (install_dir / "QI Flow.exe").read_bytes() == b"new"
    assert (install_dir / "unins000.dat").read_bytes() == b"install log"


def test_main_launch_exception_restores_previous_install(tmp_path, monkeypatch) -> None:
    install_dir = tmp_path / "Programs" / "QI Flow"
    install_dir.mkdir(parents=True)
    (install_dir / "QI Flow.exe").write_bytes(b"current-good")
    add_uninstaller(install_dir)
    archive = tmp_path / "update.zip"
    digest = make_package(archive)
    monkeypatch.setitem(_main.__globals__, "_wait_for_process_exit", lambda pid: None)
    monkeypatch.setitem(_main.__globals__, "_show_error", lambda message: None)

    def fail_launch(*args, **kwargs):
        raise OSError("launch refused")

    monkeypatch.setattr(_main.__globals__["subprocess"], "Popen", fail_launch)

    assert (
        _main(
            [
                "--pid",
                "42",
                "--archive",
                str(archive),
                "--install-dir",
                str(install_dir),
                "--sha256",
                digest,
            ]
        )
        == 1
    )
    assert (install_dir / "QI Flow.exe").read_bytes() == b"current-good"
    assert (install_dir / "unins000.dat").read_bytes() == b"install log"


def test_main_waits_for_failed_relaunch_to_exit_before_rollback(tmp_path, monkeypatch) -> None:
    install_dir = tmp_path / "Programs" / "QI Flow"
    install_dir.mkdir(parents=True)
    (install_dir / "QI Flow.exe").write_bytes(b"current-good")
    add_uninstaller(install_dir)
    archive = tmp_path / "update.zip"
    digest = make_package(archive)

    class LockedProcess:
        waited = False

        def poll(self):
            return None

        def terminate(self):
            pass

        def wait(self, timeout):
            self.waited = True
            return 0

    process = LockedProcess()
    original_replace = Path.replace

    def require_process_exit(source: Path, target: Path) -> Path:
        if (
            source == install_dir
            and target.name.startswith(".qi-flow-failed-")
            and not process.waited
        ):
            raise OSError("executable is still locked")
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", require_process_exit)
    monkeypatch.setitem(_main.__globals__, "_wait_for_process_exit", lambda pid: None)
    monkeypatch.setitem(_main.__globals__, "_wait_until_alive", lambda process: False)
    monkeypatch.setitem(_main.__globals__, "_show_error", lambda message: None)
    monkeypatch.setattr(_main.__globals__["subprocess"], "Popen", lambda *args, **kwargs: process)

    assert (
        _main(
            [
                "--pid",
                "42",
                "--archive",
                str(archive),
                "--install-dir",
                str(install_dir),
                "--sha256",
                digest,
            ]
        )
        == 1
    )
    assert (install_dir / "QI Flow.exe").read_bytes() == b"current-good"


def test_main_failed_recovery_rename_preserves_both_installations(tmp_path, monkeypatch) -> None:
    install_dir = tmp_path / "Programs" / "QI Flow"
    recovery = install_dir.with_name("QI Flow.previous")
    install_dir.mkdir(parents=True)
    (install_dir / "QI Flow.exe").write_bytes(b"current-good")
    add_uninstaller(install_dir)
    archive = tmp_path / "update.zip"
    digest = make_package(archive)
    original_replace = Path.replace

    def fail_recovery_restore(source: Path, target: Path) -> Path:
        if source == recovery and target == install_dir:
            raise OSError("recovery rename refused")
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", fail_recovery_restore)

    assert (
        run_main_without_windows_processes(
            monkeypatch, archive, install_dir, digest, launch_alive=False
        )
        == 1
    )
    assert (install_dir / "QI Flow.exe").read_bytes() == b"new"
    assert (install_dir / "unins000.dat").read_bytes() == b"install log"
    assert (recovery / "QI Flow.exe").read_bytes() == b"current-good"
    assert (recovery / "unins000.dat").read_bytes() == b"install log"


def test_main_failed_staged_placement_preserves_unknown_current_and_owned_recovery(
    tmp_path, monkeypatch
) -> None:
    install_dir = tmp_path / "Programs" / "QI Flow"
    recovery = install_dir.with_name("QI Flow.previous")
    unrelated_new = install_dir.with_name("QI Flow.new")
    install_dir.mkdir(parents=True)
    (install_dir / "QI Flow.exe").write_bytes(b"current-good")
    add_uninstaller(install_dir)
    old_install = snapshot(install_dir)
    unrelated_new.mkdir()
    (unrelated_new / "QI Flow.exe").write_bytes(b"unrelated-new")
    unrelated_before = snapshot(unrelated_new)
    data = tmp_path / "AppData" / "QI Flow" / "qi-flow.sqlite3"
    data.parent.mkdir(parents=True)
    data.write_bytes(b"private user data")
    archive = tmp_path / "update.zip"
    digest = make_package(archive)
    unknown_current = {
        "QI Flow.exe": b"independent-install",
        "unins000.exe": b"independent-uninstaller",
        "unins000.dat": b"independent-install-log",
    }
    original_replace = Path.replace

    def populate_unknown_current_before_staged_failure(source: Path, target: Path) -> Path:
        if source.parent.name.startswith(".qi-flow-update-") and target == install_dir:
            install_dir.mkdir()
            for name, contents in unknown_current.items():
                (install_dir / name).write_bytes(contents)
            raise OSError("staged placement refused")
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", populate_unknown_current_before_staged_failure)

    assert run_main_without_windows_processes(monkeypatch, archive, install_dir, digest) == 1
    assert snapshot(install_dir) == unknown_current
    assert snapshot(recovery) == old_install
    assert snapshot(unrelated_new) == unrelated_before
    assert data.read_bytes() == b"private user data"


def test_main_recovery_cleanup_failure_keeps_successful_install(tmp_path, monkeypatch) -> None:
    install_dir = tmp_path / "Programs" / "QI Flow"
    recovery = install_dir.with_name("QI Flow.previous")
    install_dir.mkdir(parents=True)
    (install_dir / "QI Flow.exe").write_bytes(b"current-good")
    add_uninstaller(install_dir)
    archive = tmp_path / "update.zip"
    digest = make_package(archive)
    original_rmtree = shutil.rmtree

    def fail_recovery_cleanup(path: Path, *args, **kwargs) -> None:
        if Path(path) == recovery:
            raise OSError("recovery cleanup failed")
        original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", fail_recovery_cleanup)

    assert run_main_without_windows_processes(monkeypatch, archive, install_dir, digest) == 0
    assert (install_dir / "QI Flow.exe").read_bytes() == b"new"
    assert (install_dir / "unins000.dat").read_bytes() == b"install log"
    assert (recovery / "QI Flow.exe").read_bytes() == b"current-good"


def test_update_swap_preserves_recovery_copy_and_changes_only_install_files(tmp_path) -> None:
    install_dir = tmp_path / "Programs" / "QI Flow"
    install_dir.mkdir(parents=True)
    (install_dir / "QI Flow.exe").write_bytes(b"old")
    add_uninstaller(install_dir)
    data_dir = tmp_path / "AppData" / "QI Flow"
    data_dir.mkdir(parents=True)
    database = data_dir / "qi-flow.sqlite3"
    database.write_bytes(b"user data")
    archive = tmp_path / "update.zip"
    digest = make_package(archive)

    _apply_update(archive, install_dir, digest)

    assert (install_dir / "QI Flow.exe").read_bytes() == b"new"
    assert (install_dir / "unins000.exe").read_bytes() == b"uninstaller"
    assert (install_dir / "unins000.dat").read_bytes() == b"install log"
    assert (install_dir.with_name("QI Flow.previous") / "QI Flow.exe").read_bytes() == b"old"
    assert database.read_bytes() == b"user data"


def test_update_refuses_to_replace_install_without_uninstall_metadata(tmp_path) -> None:
    install_dir = tmp_path / "Programs" / "QI Flow"
    install_dir.mkdir(parents=True)
    executable = install_dir / "QI Flow.exe"
    executable.write_bytes(b"old")
    archive = tmp_path / "update.zip"
    digest = make_package(archive)

    with pytest.raises(ValueError, match="uninstall"):
        _apply_update(archive, install_dir, digest)

    assert executable.read_bytes() == b"old"
    assert not install_dir.with_name("QI Flow.previous").exists()


def test_update_keeps_existing_install_when_uninstall_files_cannot_be_staged(
    tmp_path, monkeypatch
) -> None:
    install_dir = tmp_path / "Programs" / "QI Flow"
    install_dir.mkdir(parents=True)
    executable = install_dir / "QI Flow.exe"
    executable.write_bytes(b"old")
    add_uninstaller(install_dir)
    archive = tmp_path / "update.zip"
    digest = make_package(archive)
    original_copy = shutil.copy2

    def fail_uninstall_copy(source: Path, destination: Path) -> Path:
        if source.name == "unins000.dat":
            raise OSError("uninstall log is locked")
        return original_copy(source, destination)

    monkeypatch.setattr(shutil, "copy2", fail_uninstall_copy)

    with pytest.raises(OSError, match="locked"):
        _apply_update(archive, install_dir, digest)

    assert executable.read_bytes() == b"old"
    assert (install_dir / "unins000.dat").read_bytes() == b"install log"
    assert not install_dir.with_name("QI Flow.previous").exists()
    assert list(install_dir.parent.glob(".qi-flow-update-*")) == []


def test_update_swap_rejects_bad_digest_without_changing_install(tmp_path) -> None:
    install_dir = tmp_path / "Programs" / "QI Flow"
    install_dir.mkdir(parents=True)
    executable = install_dir / "QI Flow.exe"
    executable.write_bytes(b"old")
    add_uninstaller(install_dir)
    archive = tmp_path / "update.zip"
    make_package(archive)

    with pytest.raises(ValueError, match="integrity"):
        _apply_update(archive, install_dir, "0" * 64)

    assert executable.read_bytes() == b"old"


def test_update_swap_restores_the_previous_install_when_replacement_fails(
    tmp_path, monkeypatch
) -> None:
    install_dir = tmp_path / "Programs" / "QI Flow"
    install_dir.mkdir(parents=True)
    executable = install_dir / "QI Flow.exe"
    executable.write_bytes(b"old")
    add_uninstaller(install_dir)
    archive = tmp_path / "update.zip"
    digest = make_package(archive)
    original_replace = Path.replace

    def fail_staged_bundle(source: Path, target: Path) -> Path:
        if source.parent.name.startswith(".qi-flow-update-") and target == install_dir:
            raise OSError("simulated directory replacement failure")
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", fail_staged_bundle)

    with pytest.raises(OSError, match="simulated"):
        _apply_update(archive, install_dir, digest)

    assert executable.read_bytes() == b"old"
    assert not install_dir.with_name("QI Flow.previous").exists()
    assert list(install_dir.parent.glob(".qi-flow-update-*")) == []


def test_update_swap_rejects_archive_path_traversal(tmp_path) -> None:
    install_dir = tmp_path / "Programs" / "QI Flow"
    install_dir.mkdir(parents=True)
    (install_dir / "QI Flow.exe").write_bytes(b"old")
    add_uninstaller(install_dir)
    archive = tmp_path / "update.zip"
    digest = make_package(archive, member="QI Flow/../../outside.exe")

    with pytest.raises(ValueError, match="unsafe"):
        _apply_update(archive, install_dir, digest)

    assert (install_dir / "QI Flow.exe").read_bytes() == b"old"
    assert not (tmp_path / "outside.exe").exists()
    assert list(install_dir.parent.glob(".qi-flow-update-*")) == []
