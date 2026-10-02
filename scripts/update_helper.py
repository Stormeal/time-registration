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
from pathlib import Path, PurePosixPath

_MAX_ARCHIVE_BYTES = 1_000_000_000
_UNINSTALL_FILE = re.compile(r"unins(\d{3})\.(exe|dat|msg)", re.IGNORECASE)


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


def apply_update(archive: Path, install_dir: Path, expected_sha256: str) -> None:
    """Verify, stage, and atomically swap app directories with a recoverable backup."""
    archive = archive.resolve(strict=True)
    install_dir = install_dir.resolve(strict=True)
    parent = install_dir.parent
    if not install_dir.is_dir() or install_dir.name != "QI Flow":
        raise ValueError("The existing QI Flow install folder was not found.")
    if archive.is_relative_to(install_dir):
        raise ValueError("The staged update must be outside the install folder.")
    backup = parent / "QI Flow.previous"
    if backup.exists():
        raise ValueError("A previous update recovery folder already exists.")

    _verify_archive(archive, expected_sha256)
    uninstall_files: list[Path] = []
    uninstall_parts: dict[str, set[str]] = {}
    for path in install_dir.iterdir():
        match = _UNINSTALL_FILE.fullmatch(path.name)
        if path.is_file() and match is not None:
            uninstall_files.append(path)
            uninstall_parts.setdefault(match.group(1), set()).add(match.group(2).lower())
    if not any({"exe", "dat"} <= parts for parts in uninstall_parts.values()):
        raise ValueError(
            "The QI Flow uninstall files are missing. Reinstall QI Flow before updating."
        )

    staging = Path(tempfile.mkdtemp(prefix=".qi-flow-update-", dir=parent))
    try:
        bundle = _extract_bundle(archive, staging)
        for path in uninstall_files:
            shutil.copy2(path, bundle / path.name)
        install_dir.replace(backup)
        try:
            bundle.replace(install_dir)
        except OSError:
            backup.replace(install_dir)
            raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)


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
    backup = install_dir.with_name("QI Flow.previous")
    try:
        _wait_for_process_exit(args.pid)
        apply_update(args.archive, install_dir, args.sha256)
        process = subprocess.Popen([str(install_dir / "QI Flow.exe")], cwd=install_dir)
        if _wait_until_alive(process):
            with contextlib.suppress(OSError):
                shutil.rmtree(backup)
            return 0
        if process.poll() is None:
            process.terminate()
    except Exception as error:  # the helper reports only a concise user-actionable failure
        _show_error(str(error))
    if backup.exists():
        failed = install_dir.with_name("QI Flow.failed")
        if failed.exists():
            shutil.rmtree(failed)
        if install_dir.exists():
            install_dir.replace(failed)
        backup.replace(install_dir)
        if failed.exists():
            shutil.rmtree(failed)
        subprocess.Popen([str(install_dir / "QI Flow.exe")], cwd=install_dir)
    else:
        _show_error("QI Flow could not apply the update. Your existing installation is unchanged.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
