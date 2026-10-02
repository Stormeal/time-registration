"""Stable per-user launcher and updater for the replaceable QI Flow bundle."""

from __future__ import annotations

import argparse
import ctypes
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from ctypes import wintypes
from pathlib import Path
from typing import Protocol

from scripts.update_runtime import apply_update, install_lock, recover_install

_TOKEN_PATTERN = re.compile(r"[0-9a-f]{32}")


class Process(Protocol):
    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...
    def kill(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...


def _start(command: list[str], cwd: Path) -> subprocess.Popen[bytes]:
    return subprocess.Popen(command, cwd=cwd)


def launch(
    install_root: Path,
    arguments: list[str],
    start: Callable[[list[str], Path], object] = _start,
) -> None:
    """Recover if needed, then launch the installed app through its stable path."""
    root = install_root.resolve(strict=True)
    with install_lock(root):
        recover_install(root)
        executable = root / "current" / "QI Flow.exe"
        if not executable.is_file():
            raise RuntimeError("QI Flow is missing. Run the per-user recovery installer.")
        start([str(executable), *arguments], executable.parent)


def trial_launch(
    executable: Path,
    transaction_id: str,
    start: Callable[[list[str], Path], Process] = _start,
    timeout_seconds: float = 20,
) -> bool:
    """Accept a new bundle only after it reports initialization complete."""
    if _TOKEN_PATTERN.fullmatch(transaction_id) is None:
        raise ValueError("Invalid update readiness token.")
    marker = executable.parent.parent / "update-work" / f"ready-{transaction_id}"
    marker.parent.mkdir(exist_ok=True)
    marker.unlink(missing_ok=True)
    process = start([str(executable), "--update-ready", transaction_id], executable.parent)
    deadline = time.monotonic() + timeout_seconds
    accepted = False
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                return False
            if marker.is_file() and marker.read_text(encoding="ascii") == transaction_id:
                grace_deadline = min(deadline, time.monotonic() + 0.5)
                while time.monotonic() < grace_deadline:
                    if process.poll() is not None:
                        return False
                    time.sleep(0.05)
                accepted = process.poll() is None
                return accepted
            time.sleep(0.05)
        return False
    finally:
        marker.unlink(missing_ok=True)
        if not accepted and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def _wait_for_process_exit(process_id: int, timeout_seconds: int = 900) -> None:
    if os.name != "nt":
        raise RuntimeError("The QI Flow updater can only run on Windows.")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    process = kernel32.OpenProcess(0x00100000, False, process_id)
    if not process:
        if ctypes.get_last_error() == 87:  # ERROR_INVALID_PARAMETER: process already exited.
            return
        raise OSError("QI Flow could not verify that the previous process closed.")
    try:
        if kernel32.WaitForSingleObject(process, timeout_seconds * 1000) != 0:
            raise TimeoutError("QI Flow did not close in time to install the update.")
    finally:
        kernel32.CloseHandle(process)


def _show_error(message: str) -> None:
    if os.name == "nt":
        ctypes.windll.user32.MessageBoxW(None, message, "QI Flow update", 0x10)
    else:
        print(message, file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    arguments = list(argv if argv is not None else sys.argv[1:])
    root = Path(sys.executable).resolve().parent
    if arguments and arguments[0] == "--apply-update":
        parser = argparse.ArgumentParser()
        parser.add_argument("--apply-update", action="store_true")
        parser.add_argument("--pid", type=int, required=True)
        parser.add_argument("--archive", type=Path, required=True)
        parser.add_argument("--sha256", required=True)
        options = parser.parse_args(arguments)
        try:
            with install_lock(root):
                _wait_for_process_exit(options.pid)
                apply_update(options.archive, root, options.sha256, trial_launch)
            return 0
        except Exception as error:
            _show_error(
                f"QI Flow could not complete the update: {error}\n\n"
                "Run the recovery installer if QI Flow does not reopen."
            )
            try:
                launch(root, [])
            except Exception:
                _show_error(
                    "QI Flow could not restore its application files. "
                    "Run the per-user recovery installer. Your time data remains in AppData."
                )
            return 1
    try:
        launch(root, arguments)
    except Exception as error:
        _show_error(f"QI Flow could not start: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
