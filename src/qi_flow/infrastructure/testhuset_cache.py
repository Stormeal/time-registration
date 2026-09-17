"""Atomic, allowlisted task-name cache. Authentication data has no representation here."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from qi_flow.domain.testhuset import ProjectTask


class JsonTaskCache:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> tuple[ProjectTask, ...]:
        if not self.path.exists():
            return ()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                raise ValueError
            tasks = tuple(ProjectTask(**item) for item in data)
            if len({task.id for task in tasks}) != len(tasks):
                raise ValueError
            return tasks
        except (TypeError, ValueError, KeyError) as error:
            raise ValueError("Task cache is invalid. Scan Testhuset tasks again.") from error

    def replace(self, tasks: tuple[ProjectTask, ...]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        try:
            temporary.write_text(
                json.dumps([asdict(task) for task in tasks], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)
