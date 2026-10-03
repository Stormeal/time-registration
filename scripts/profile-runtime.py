"""Measure real desktop refreshes and backup scans using disposable synthetic history."""

import json
import os
import sqlite3
import statistics
import tempfile
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from shutil import copyfile
from time import perf_counter

from PySide6.QtWidgets import QApplication

from qi_flow.application.time_tracking import TimeTrackingApplicationService
from qi_flow.domain.models import Deduction, DeductionId, DeductionKind, SessionId, WorkSession
from qi_flow.infrastructure.backups import BackupManager
from qi_flow.infrastructure.sqlite.database import SQLiteDatabase
from qi_flow.infrastructure.sqlite.repositories import SQLiteUnitOfWork
from qi_flow.infrastructure.system import UuidIdentifierGenerator
from qi_flow.ui.today_page import TodayPage


class Clock:
    def now(self):
        return datetime(2026, 10, 3, 15, tzinfo=UTC)


def main():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    clock = Clock()
    with tempfile.TemporaryDirectory(prefix="qi-flow-profile-") as temporary:
        root = Path(temporary)
        database = SQLiteDatabase(root / "history.sqlite3")
        database.initialize()

        def uow():
            return SQLiteUnitOfWork(database)

        # Adapter-level seeding avoids timing unrelated manual-entry overlap validation.
        with uow() as transaction:
            for index in range(10_000):
                start = clock.now().replace(hour=7) - timedelta(days=index)
                identifier = SessionId(f"work-{index}")
                transaction.sessions.add(
                    WorkSession(
                        identifier,
                        start,
                        start + timedelta(hours=8),
                        created_at=start,
                        updated_at=start,
                    )
                )
                transaction.deductions.add(
                    Deduction(
                        DeductionId(f"lunch-{index}"),
                        identifier,
                        DeductionKind.LUNCH,
                        start + timedelta(hours=4),
                        start + timedelta(hours=4, minutes=30),
                        created_at=start,
                        updated_at=start,
                    )
                )
        with closing(database.connect()) as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        folder = root / "backups"
        folder.mkdir()
        for index in range(30):
            copyfile(database.database_file, folder / f"qi-flow-backup-{index:02}.sqlite3")
        service = TimeTrackingApplicationService(uow, clock, UuidIdentifierGenerator())
        backups = BackupManager(database, folder, uow, clock)
        page = TodayPage(service)
        counts = {"selects": 0, "integrity_checks": 0}
        connect = sqlite3.connect

        def trace(statement):
            if statement.lstrip().upper().startswith("SELECT"):
                counts["selects"] += 1
            if "PRAGMA INTEGRITY_CHECK" in statement.upper():
                counts["integrity_checks"] += 1

        def traced_connect(*args, **kwargs):
            connection = connect(*args, **kwargs)
            connection.set_trace_callback(trace)
            return connection

        def measure(operation):
            samples = []
            for _ in range(5):
                counts.update(selects=0, integrity_checks=0)
                start = perf_counter()
                operation()
                samples.append({"milliseconds": (perf_counter() - start) * 1000, **counts})
            return {
                "first_ms": round(samples[0]["milliseconds"], 2),
                "first_integrity_checks": samples[0]["integrity_checks"],
                "median_ms": round(statistics.median(s["milliseconds"] for s in samples), 2),
                "selects_per_run": samples[-1]["selects"],
                "integrity_checks_per_run": samples[-1]["integrity_checks"],
            }

        sqlite3.connect = traced_connect
        try:
            result = {
                "sessions": 10_000,
                "deductions": 10_000,
                "backup_files": 30,
                "database_bytes": database.database_file.stat().st_size,
                "today_refresh": measure(page.refresh),
                "settings_backup_reads": measure(
                    lambda: (backups.status(), backups.list_backups())
                ),
            }
        finally:
            sqlite3.connect = connect
            page.close()
            page.deleteLater()
            app.processEvents()
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
