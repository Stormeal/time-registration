"""Resolve and create QI Flow runtime paths."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QStandardPaths


@dataclass(frozen=True, slots=True)
class AppPaths:
    """All writable paths used by the application."""

    data_dir: Path
    database_file: Path
    backup_dir: Path
    log_dir: Path

    @classmethod
    def from_qt(cls) -> AppPaths:
        """Build paths from Qt's per-user application-data location.

        ``QI_FLOW_DATA_DIR`` is intentionally supported for tests and local diagnostics.
        Packaged application behavior uses QStandardPaths.
        """
        override = os.environ.get("QI_FLOW_DATA_DIR")
        if override:
            return cls.for_root(Path(override))

        location = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppLocalDataLocation
        )
        if not location:
            raise RuntimeError("Windows did not provide an application-data directory")
        return cls.for_root(Path(location))

    @classmethod
    def for_root(cls, root: Path) -> AppPaths:
        root = root.resolve()
        return cls(
            data_dir=root,
            database_file=root / "qi-flow.sqlite3",
            backup_dir=root / "backups",
            log_dir=root / "logs",
        )

    def ensure(self) -> None:
        """Create directories without touching the database file."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
