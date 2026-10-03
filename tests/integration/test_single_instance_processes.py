"""Real Windows ownership races; every process uses only temporary data."""

from __future__ import annotations

import json
import os
import queue
import sqlite3
import subprocess
import sys
import threading
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows native process locking")

_WORKER = r"""
import inspect
import json
import os
import sys
from pathlib import Path
from PySide6.QtCore import QCoreApplication
from PySide6.QtNetwork import QLocalServer
from qi_flow.infrastructure.single_instance import SingleInstanceGuard
from qi_flow.infrastructure.sqlite.database import SQLiteDatabase

app = QCoreApplication([])
root, key, block_listen = Path(sys.argv[1]), sys.argv[2], sys.argv[3] == "1"
original_listen = QLocalServer.listen
if block_listen:
    def listen(self, name):
        print(json.dumps({"event": "before_listen"}), flush=True)
        assert sys.stdin.readline().strip() == "listen"
        return original_listen(self, name)
    QLocalServer.listen = listen

# The compatibility branch permits running this same regression against the old
# socket-only implementation, proving the race rather than a missing argument.
kwargs = {}
if "lock_path" in inspect.signature(SingleInstanceGuard).parameters:
    kwargs["lock_path"] = root / "qi-flow.instance.lock"
guard = SingleInstanceGuard(key, **kwargs)
guard.focus_requested.connect(lambda: print(json.dumps({"event": "focus"}), flush=True))
print(json.dumps({"event": "barrier", "pid": os.getpid()}), flush=True)
assert sys.stdin.readline().strip() == "acquire"
try:
    acquired = guard.try_acquire()
except OSError as error:
    print(json.dumps({"event": "error", "kind": type(error).__name__}), flush=True)
    sys.exit(0)
if acquired:
    database = SQLiteDatabase(root / "ownership.sqlite3")
    database.initialize()
    with database.transaction() as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS owners (pid INTEGER NOT NULL)")
        connection.execute("INSERT INTO owners VALUES (?)", (os.getpid(),))
print(json.dumps({"event": "result", "acquired": acquired}), flush=True)
for command in sys.stdin:
    command = command.strip()
    if command == "events":
        app.processEvents()
        print(json.dumps({"event": "events_done"}), flush=True)
    elif command == "stop":
        guard.release()
        break
"""


class _Process:
    def __init__(self, root: Path, key: str, *, block_listen: bool = False) -> None:
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
        environment["QT_QPA_PLATFORM"] = "offscreen"
        self.process = subprocess.Popen(
            [sys.executable, "-u", "-c", _WORKER, str(root), key, str(int(block_listen))],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        self.messages: queue.Queue[str] = queue.Queue()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        self.pid: int | None = None

    def _read(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.messages.put(line)
        self.messages.put("")

    def receive(self) -> dict[str, object]:
        try:
            line = self.messages.get(timeout=15)
        except queue.Empty:
            self.kill()
            _, errors = self.process.communicate(timeout=5)
            pytest.fail(f"Child process timed out: {errors}")
        if not line:
            assert self.process.stderr is not None
            pytest.fail(f"Child process exited: {self.process.stderr.read()}")
        result: dict[str, object] = json.loads(line)
        return result

    def send(self, command: str) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(command + "\n")
        self.process.stdin.flush()

    def barrier(self) -> None:
        message = self.receive()
        assert message["event"] == "barrier"
        self.pid = int(str(message["pid"]))

    def kill(self) -> None:
        subprocess.run(
            ["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=5,
            check=False,
        )

    def close(self) -> None:
        if self.process.poll() is None:
            self.kill()
        self.process.wait(timeout=5)
        self.reader.join(timeout=5)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if stream is not None:
                stream.close()


def _owners(root: Path) -> list[int]:
    with sqlite3.connect(root / "ownership.sqlite3") as connection:
        return [row[0] for row in connection.execute("SELECT pid FROM owners")]


def test_second_process_cannot_initialize_database_before_primary_listens(tmp_path: Path) -> None:
    key = uuid.uuid4().hex
    first = _Process(tmp_path, key, block_listen=True)
    second = _Process(tmp_path, key)
    try:
        first.barrier()
        second.barrier()
        first.send("acquire")
        assert first.receive() == {"event": "before_listen"}
        second.send("acquire")
        second_result = second.receive()
        first.send("listen")
        first_result = first.receive()
        assert [first_result, second_result] == [
            {"event": "result", "acquired": True},
            {"event": "result", "acquired": False},
        ]
        assert _owners(tmp_path) == [first.pid]
    finally:
        first.close()
        second.close()


def test_simultaneous_launches_initialize_database_in_exactly_one_process(tmp_path: Path) -> None:
    key = uuid.uuid4().hex
    processes = [_Process(tmp_path, key), _Process(tmp_path, key)]
    try:
        for process in processes:
            process.barrier()
        for process in processes:
            process.send("acquire")
        results = [process.receive() for process in processes]
        assert all(result["event"] == "result" for result in results)
        assert sum(result["acquired"] is True for result in results) == 1
        winner = processes[
            next(index for index, result in enumerate(results) if result["acquired"])
        ]
        assert _owners(tmp_path) == [winner.pid]
    finally:
        for process in processes:
            process.close()


def test_second_process_requests_focus_without_initializing_database(tmp_path: Path) -> None:
    key = uuid.uuid4().hex
    first = _Process(tmp_path, key)
    second = _Process(tmp_path, key)
    try:
        first.barrier()
        second.barrier()
        first.send("acquire")
        assert first.receive() == {"event": "result", "acquired": True}
        second.send("acquire")
        assert second.receive() == {"event": "result", "acquired": False}
        first.send("events")
        assert first.receive() == {"event": "focus"}
        assert first.receive() == {"event": "events_done"}
        assert _owners(tmp_path) == [first.pid]
    finally:
        first.close()
        second.close()


@pytest.mark.parametrize("crash", [False, True])
def test_later_process_recovers_ownership_after_release_or_crash(
    tmp_path: Path, crash: bool
) -> None:
    root = tmp_path / "Æøå-時間"
    root.mkdir()
    key = uuid.uuid4().hex
    first = _Process(root, key)
    second = _Process(root, key)
    try:
        first.barrier()
        second.barrier()
        first.send("acquire")
        assert first.receive() == {"event": "result", "acquired": True}
        if crash:
            first.kill()
        else:
            first.send("stop")
        first.process.wait(timeout=5)
        second.send("acquire")
        assert second.receive() == {"event": "result", "acquired": True}
        assert _owners(root) == [first.pid, second.pid]
    finally:
        first.close()
        second.close()


def test_distinct_data_directories_can_have_independent_owners(tmp_path: Path) -> None:
    roots = [tmp_path / "first", tmp_path / "second"]
    for root in roots:
        root.mkdir()
    key = uuid.uuid4().hex
    processes = [_Process(root, key) for root in roots]
    try:
        for process in processes:
            process.barrier()
            process.send("acquire")
            assert process.receive() == {"event": "result", "acquired": True}
        for root, process in zip(roots, processes, strict=True):
            assert _owners(root) == [process.pid]
    finally:
        for process in processes:
            process.close()


def test_process_reports_lock_permission_failure_without_initializing_database(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "qi-flow.instance.lock"
    lock_path.write_bytes(b"")
    lock_path.chmod(0o444)
    process = _Process(tmp_path, uuid.uuid4().hex)
    try:
        process.barrier()
        process.send("acquire")
        result = process.receive()
        assert result == {"event": "error", "kind": "PermissionError"}
        assert not (tmp_path / "ownership.sqlite3").exists()
    finally:
        process.close()
        lock_path.chmod(0o666)
