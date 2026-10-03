"""Standalone, standard-library-only Windows install swapper used by QI Flow."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

_MAX_ARCHIVE_BYTES = 1_000_000_000
_UNINSTALL_FILE = re.compile(r"unins(\d{3})\.(exe|dat|msg)", re.IGNORECASE)


@dataclass
class _SwapState:
    recovery: Path | None = None
    placed_current: bool = False


def _check_root(path: Path, parent: Path) -> None:
    """Reject a replaced directory link before a rename or recursive removal."""
    if path.resolve(strict=False) != parent.resolve(strict=True) / path.name:
        raise ValueError("An update folder points outside the installation location.")


def _check_child(path: Path, root: Path) -> None:
    if not path.resolve(strict=False).is_relative_to(root.resolve(strict=True)):
        raise ValueError("An update file points outside its expected folder.")


def _remove_owned_tree(path: Path, parent: Path) -> None:
    if not path.exists():
        return
    _check_root(path, parent)
    for child in path.rglob("*"):
        _check_child(child, path)
    shutil.rmtree(path)


def _verify_archive(archive: Path, expected_sha256: str) -> None:
    digest = hashlib.sha256()
    with archive.open("rb") as package:
        while chunk := package.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != expected_sha256:
        raise ValueError("Update package integrity check failed.")


def _extract_bundle(archive: Path, staging: Path) -> Path:
    total_size = 0
    with zipfile.ZipFile(archive) as package:
        members = package.infolist()
        if not members or len(members) > 50_000:
            raise ValueError("Update package contents are not valid.")
        for member in members:
            name = member.filename
            path = PurePosixPath(name)
            mode = member.external_attr >> 16
            total_size += member.file_size
            if (
                path.is_absolute()
                or ".." in path.parts
                or "\\" in name
                or not path.parts
                or path.parts[0] != "QI Flow"
                or stat.S_ISLNK(mode)
                or total_size > _MAX_ARCHIVE_BYTES
            ):
                raise ValueError("Update package contains an unsafe file path.")
        package.extractall(staging)
    bundle = staging / "QI Flow"
    if not (bundle / "QI Flow.exe").is_file():
        raise ValueError("Update package is missing QI Flow.exe.")
    return bundle


def _wait_for_process_exit(process_id: int, timeout_seconds: int = 900) -> None:
    if os.name != "nt":
        raise RuntimeError("The QI Flow updater can only run on Windows.")
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    synchronize = 0x00100000
    process = kernel32.OpenProcess(synchronize, False, process_id)
    if not process:
        return
    try:
        result = kernel32.WaitForSingleObject(process, timeout_seconds * 1000)
        if result != 0:
            raise TimeoutError("QI Flow did not close in time to install the update.")
    finally:
        kernel32.CloseHandle(process)


def apply_update(
    archive: Path, install_dir: Path, expected_sha256: str, state: _SwapState | None = None
) -> None:
    """Verify, stage, and atomically swap app directories with a recoverable backup."""
    archive = archive.resolve(strict=True)
    install_dir = install_dir.resolve(strict=True)
    parent = install_dir.parent
    state = state if state is not None else _SwapState()
    if not install_dir.is_dir() or install_dir.name != "QI Flow":
        raise ValueError("The existing QI Flow install folder was not found.")
    if archive.is_relative_to(install_dir):
        raise ValueError("The staged update must be outside the install folder.")
    backup = parent / "QI Flow.previous"
    _check_root(install_dir, parent)
    _check_root(backup, parent)
    if backup.exists():
        raise ValueError("A previous update recovery folder already exists.")

    _verify_archive(archive, expected_sha256)
    uninstall_files: list[Path] = []
    uninstall_parts: dict[str, set[str]] = {}
    for path in install_dir.iterdir():
        match = _UNINSTALL_FILE.fullmatch(path.name)
        if path.is_file() and match is not None:
            _check_child(path, install_dir)
            uninstall_files.append(path)
            uninstall_parts.setdefault(match.group(1), set()).add(match.group(2).lower())
    if not any({"exe", "dat"} <= parts for parts in uninstall_parts.values()):
        raise ValueError(
            "The QI Flow uninstall files are missing. Reinstall QI Flow before updating."
        )

    staging = Path(tempfile.mkdtemp(prefix=".qi-flow-update-", dir=parent))
    try:
        _check_root(staging, parent)
        bundle = _extract_bundle(archive, staging)
        _check_child(bundle, staging)
        for path in uninstall_files:
            destination = bundle / path.name
            _check_child(path, install_dir)
            _check_child(destination, staging)
            shutil.copy2(path, destination)
        _check_root(install_dir, parent)
        _check_root(backup, parent)
        install_dir.replace(backup)
        state.recovery = backup
        try:
            _check_child(bundle, staging)
            bundle.replace(install_dir)
            state.placed_current = True
        except OSError:
            _check_root(backup, parent)
            _check_root(install_dir, parent)
            backup.replace(install_dir)
            state.recovery = None
            raise
    finally:
        if staging.exists():
            # Cleanup must never turn an installed update into a rollback.
            with contextlib.suppress(OSError, ValueError):
                _remove_owned_tree(staging, parent)


def _restore_owned_recovery(install_dir: Path, state: _SwapState) -> bool:
    recovery = state.recovery
    if recovery is None or not recovery.exists():
        return False
    parent = install_dir.parent
    _check_root(recovery, parent)
    _check_root(install_dir, parent)
    if install_dir.exists() and not state.placed_current:
        raise RuntimeError(
            "Another installation occupies the QI Flow folder. "
            "It and the previous installation were both retained."
        )
    displaced: Path | None = None
    if install_dir.exists():
        # Reserve a name belonging only to this attempt; never touch QI Flow.failed.
        displaced = Path(tempfile.mkdtemp(prefix=".qi-flow-failed-", dir=parent))
        displaced.rmdir()
        _check_root(install_dir, parent)
        _check_root(displaced, parent)
        install_dir.replace(displaced)
    try:
        _check_root(recovery, parent)
        _check_root(install_dir, parent)
        recovery.replace(install_dir)
    except OSError:
        if displaced is not None and not install_dir.exists():
            _check_root(displaced, parent)
            _check_root(install_dir, parent)
            displaced.replace(install_dir)
        raise
    state.recovery = None
    state.placed_current = False
    if displaced is not None:
        with contextlib.suppress(OSError, ValueError):
            _remove_owned_tree(displaced, parent)
    return True


def _wait_until_alive(process: subprocess.Popen[bytes], timeout_seconds: int = 15) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        time.sleep(0.25)
    return process.poll() is None


def _show_error(message: str) -> None:
    if os.name == "nt":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "QI Flow update", 0x10)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--install-dir", required=True, type=Path)
    parser.add_argument("--sha256", required=True)
    args = parser.parse_args(argv)
    install_dir = args.install_dir.resolve(strict=True)
    state = _SwapState()
    try:
        _wait_for_process_exit(args.pid)
        apply_update(args.archive, install_dir, args.sha256, state)
        executable = install_dir / "QI Flow.exe"
        _check_child(executable, install_dir)
        process = subprocess.Popen([str(executable)], cwd=install_dir)
        if _wait_until_alive(process):
            with contextlib.suppress(OSError, ValueError):
                if state.recovery is not None:
                    _remove_owned_tree(state.recovery, install_dir.parent)
            return 0
        if process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired) as error:
                _show_error(f"QI Flow did not exit after the failed update launch: {error}")
                return 1
    except Exception as error:  # the helper reports only a concise user-actionable failure
        _show_error(str(error))
        if state.recovery is None:
            return 1
    try:
        restored = _restore_owned_recovery(install_dir, state)
    except Exception as error:
        _show_error(f"QI Flow could not restore the previous installation: {error}")
        return 1
    if restored:
        try:
            executable = install_dir / "QI Flow.exe"
            _check_child(executable, install_dir)
            subprocess.Popen([str(executable)], cwd=install_dir)
        except Exception as error:
            _show_error(
                f"The previous QI Flow installation was restored but could not start: {error}"
            )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
