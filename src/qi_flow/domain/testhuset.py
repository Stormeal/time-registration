"""Strict decimal-hour rules and safe, non-secret Testhuset identities."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal


@dataclass(frozen=True, slots=True)
class ProjectTask:
    id: str
    project_name: str
    task_name: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[0-9]+-[0-9]+", self.id):
            raise ValueError("Invalid Testhuset task identifier.")
        if (
            not isinstance(self.project_name, str)
            or not isinstance(self.task_name, str)
            or not self.project_name.strip()
            or not self.task_name.strip()
        ):
            raise ValueError("Testhuset task names must not be empty.")

    @property
    def label(self) -> str:
        return f"{self.project_name} / {self.task_name}"


def parse_hours(value: str) -> Decimal:
    """Accept plain hours with up to two decimals; never guess grouping separators."""
    value = value.strip()
    if not value:
        return Decimal(0)
    if not re.fullmatch(r"[0-9]+(?:[.,][0-9]{1,2})?", value):
        raise ValueError("Ambiguous Testhuset hours. Use plain hours with at most two decimals.")
    return Decimal(value.replace(",", "."))


def decimal_hours(seconds: int) -> str:
    if seconds < 0:
        raise ValueError("Net work cannot be negative.")
    return str((Decimal(seconds) / 3600).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
