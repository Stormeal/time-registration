"""Standard-library transaction and recovery rules for per-user app updates."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import stat
import time
import uuid
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path, PurePosixPath

_MAX_ARCHIVE_BYTES = 1_000_000_000
_JOURNAL_NAME = "update-transaction.json"
_WORK_NAME = "update-work"
_ID_PATTERN = re.compile(r"[0-9a-f]{32}")
_PHASES = {"prepared", "old_moved", "new_moved", "committed"}


def _paths(root: Path, transaction_id: str) -> tuple[Path, Path, Path]:
    work = root / _WORK_NAME
    return (
        work / f"stage-{transaction_id}",
        work / f"previous-{transaction_id}",
        work / f"failed-{transaction_id}",
    )


def _read_record(path: Path, root: Path) -> dict[str, str]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError("The update transaction record is invalid.") from error
    if not isinstance(value, dict):
        raise ValueError("The update transaction record is invalid.")
    transaction_id = value.get("id")
    phase = value.get("phase")
    previous = value.get("previous")
    staging = value.get("staging")
    digest = value.get("sha256")
    if (
        not isinstance(transaction_id, str)
        or _ID_PATTERN.fullmatch(transaction_id) is None
        or phase not in _PHASES
        or not isinstance(previous, str)
        or not isinstance(staging, str)
        or not isinstance(digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
    ):
        raise ValueError("The update transaction record is invalid.")
    stage_dir, previous_dir, _failed_dir = _paths(root, transaction_id)
    if (
        previous != previous_dir.relative_to(root).as_posix()
        or staging != (stage_dir / "QI Flow").relative_to(root).as_posix()
    ):
        raise ValueError("The update transaction contains an unsafe path.")
    return {
        "id": transaction_id,
        "phase": phase,
        "previous": previous,
        "staging": staging,
        "sha256": digest,
    }


def _write_record(path: Path, record: dict[str, str]) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(record, output, sort_keys=True)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


def _verify_and_extract(archive: Path, digest: str, stage_dir: Path) -> Path:
    calculated = hashlib.sha256()
    with archive.open("rb") as input_file:
        while chunk := input_file.read(1024 * 1024):
            calculated.update(chunk)
    if calculated.hexdigest() != digest:
        raise ValueError("Update package integrity check failed.")
    total = 0
    with zipfile.ZipFile(archive) as package:
        members = package.infolist()
        if not members or len(members) > 50_000:
            raise ValueError("Update package contents are invalid.")
        for member in members:
            name = member.filename
            path = PurePosixPath(name)
            total += member.file_size
            if (
                path.is_absolute()
                or ".." in path.parts
                or "\\" in name
                or not path.parts
                or path.parts[0] != "QI Flow"
                or stat.S_ISLNK(member.external_attr >> 16)
                or total > _MAX_ARCHIVE_BYTES
            ):
                raise ValueError("Update package contains an unsafe file path.")
        package.extractall(stage_dir)
    bundle = stage_dir / "QI Flow"
    if not (bundle / "QI Flow.exe").is_file():
        raise ValueError("Update package is missing QI Flow.exe.")
    return bundle


def retry_cleanup(install_root: Path) -> None:
    """Retry deletion of committed previous bundles without blocking launches."""
    root = install_root.resolve(strict=True)
    work = root / _WORK_NAME
    journal = root / _JOURNAL_NAME
    if journal.exists():
        record = _read_record(journal, root)
        if record["phase"] == "committed":
            work.mkdir(exist_ok=True)
            os.replace(journal, work / f"cleanup-{record['id']}.json")
    if not work.exists():
        return
    for manifest in work.glob("cleanup-*.json"):
        record = _read_record(manifest, root)
        if record["phase"] != "committed" or manifest.name != f"cleanup-{record['id']}.json":
            raise ValueError("The update cleanup record is invalid.")
        previous = root / record["previous"]
        try:
            if previous.exists():
                shutil.rmtree(previous)
            manifest.unlink()
        except OSError:
            continue


def recover_install(install_root: Path) -> str:
    """Restore a usable current bundle after an interrupted update."""
    root = install_root.resolve(strict=True)
    current = root / "current"
    journal = root / _JOURNAL_NAME
    if not journal.exists():
        if not current.is_dir():
            raise RuntimeError(
                "The QI Flow application bundle is missing; run the recovery installer."
            )
        retry_cleanup(root)
        return "unchanged"
    record = _read_record(journal, root)
    if record["phase"] == "committed":
        if not current.is_dir():
            raise RuntimeError(
                "The updated application bundle is missing; run the recovery installer."
            )
        retry_cleanup(root)
        return (
            "cleanup_pending"
            if (root / _WORK_NAME / f"cleanup-{record['id']}.json").exists()
            else "unchanged"
        )

    stage_dir, previous, failed = _paths(root, record["id"])
    if previous.is_dir():
        if current.exists():
            if failed.exists():
                shutil.rmtree(failed)
            current.replace(failed)
        previous.replace(current)
        journal.unlink()
        for leftover in (failed, stage_dir):
            with contextlib.suppress(OSError):
                if leftover.exists():
                    shutil.rmtree(leftover)
        return "restored"
    if current.is_dir() and (record["phase"] == "old_moved" or failed.is_dir()):
        if not (current / "QI Flow.exe").is_file():
            raise RuntimeError(
                "The restored QI Flow bundle is incomplete; run the recovery installer."
            )
        journal.unlink()
        for leftover in (failed, stage_dir):
            with contextlib.suppress(OSError):
                if leftover.exists():
                    shutil.rmtree(leftover)
        return "restored"
    if record["phase"] == "prepared" and current.is_dir():
        journal.unlink()
        with contextlib.suppress(OSError):
            if stage_dir.exists():
                shutil.rmtree(stage_dir)
        return "unchanged"
    raise RuntimeError("The previous QI Flow bundle is missing; run the recovery installer.")


def apply_update(
    archive: Path,
    install_root: Path,
    expected_sha256: str,
    trial: Callable[[Path, str], bool],
) -> None:
    """Verify, replace, and trial-launch only the replaceable app bundle."""
    root = install_root.resolve(strict=True)
    if not (root / "QI Flow Launcher.exe").is_file() or not (root / "current").is_dir():
        raise ValueError("The QI Flow installation folder was not found.")
    archive = archive.resolve(strict=True)
    if archive.is_relative_to(root):
        raise ValueError("The staged update must be outside the install folder.")
    recover_install(root)
    transaction_id = uuid.uuid4().hex
    stage_dir, previous, _failed = _paths(root, transaction_id)
    stage_dir.mkdir(parents=True)
    try:
        bundle = _verify_and_extract(archive, expected_sha256, stage_dir)
        record = {
            "id": transaction_id,
            "phase": "prepared",
            "previous": previous.relative_to(root).as_posix(),
            "staging": bundle.relative_to(root).as_posix(),
            "sha256": expected_sha256,
        }
        journal = root / _JOURNAL_NAME
        _write_record(journal, record)
        try:
            (root / "current").replace(previous)
            record["phase"] = "old_moved"
            _write_record(journal, record)
            bundle.replace(root / "current")
            record["phase"] = "new_moved"
            _write_record(journal, record)
            if not trial(root / "current" / "QI Flow.exe", transaction_id):
                raise RuntimeError("The updated QI Flow did not pass startup validation.")
            record["phase"] = "committed"
            _write_record(journal, record)
        except Exception:
            recover_install(root)
            raise
        retry_cleanup(root)
    finally:
        with contextlib.suppress(OSError):
            if stage_dir.exists():
                shutil.rmtree(stage_dir)


@contextlib.contextmanager
def install_lock(install_root: Path, timeout_seconds: int = 900) -> Iterator[None]:
    """Serialize launch/recovery/update across processes for one installation."""
    import msvcrt

    root = install_root.resolve(strict=True)
    lock_path = root / "update.lock"
    deadline = time.monotonic() + timeout_seconds
    try:
        with lock_path.open("xb") as created:
            created.write(b"0")
    except FileExistsError:
        pass
    with lock_path.open("r+b") as lock_file:
        while True:
            try:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        "QI Flow is waiting too long for another update or launch."
                    ) from None
                time.sleep(0.2)
        try:
            yield
        finally:
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
