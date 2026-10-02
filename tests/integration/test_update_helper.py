from __future__ import annotations

import hashlib
import runpy
import shutil
import zipfile
from pathlib import Path

import pytest

_HELPER = runpy.run_path("scripts/update_helper.py")
_apply_update = _HELPER["apply_update"]


def make_package(
    path: Path, *, member: str = "QI Flow/QI Flow.exe", contents: bytes = b"new"
) -> str:
    with zipfile.ZipFile(path, "w") as package:
        package.writestr(member, contents)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def add_uninstaller(install_dir: Path) -> None:
    (install_dir / "unins000.exe").write_bytes(b"uninstaller")
    (install_dir / "unins000.dat").write_bytes(b"install log")


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
