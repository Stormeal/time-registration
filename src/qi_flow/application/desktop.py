"""Ports for desktop files, startup preferences and explicit release operations."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

from qi_flow.application.dto import DaySummaryView


class RuntimeDirectories(Protocol):
    @property
    def data_dir(self) -> Path: ...
    @property
    def database_file(self) -> Path: ...
    @property
    def backup_dir(self) -> Path: ...
    @property
    def log_dir(self) -> Path: ...


class TimesheetExporter(Protocol):
    def write_summary(self, path: Path, summaries: Iterable[DaySummaryView]) -> None: ...
    def write_detailed(self, path: Path, start: date, end: date) -> None: ...


class StartupPreferences(Protocol):
    def is_enabled(self) -> bool: ...
    def set_enabled(self, enabled: bool) -> None: ...


@dataclass(frozen=True, slots=True)
class AvailableUpdate:
    version: str
    download_url: str
    sha256: str
    size: int


class UpdateError(Exception):
    """An approved, safe-to-display release failure."""


class ReleaseOperations(Protocol):
    def check(self, *, cancelled: Callable[[], bool] = lambda: False) -> AvailableUpdate | None: ...
    def download(
        self,
        update: AvailableUpdate,
        destination: Path,
        progress: Callable[[int, int], None] | None = None,
        *,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> Path: ...


@dataclass(frozen=True)
class RestartCommand:
    program: str
    arguments: tuple[str, ...]
    working_directory: str = ""
