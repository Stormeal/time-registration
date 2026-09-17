"""CSV rendering for the user-facing, rounded timesheet record."""

from __future__ import annotations

import csv
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from qi_flow.application.dto import DaySummaryView
from qi_flow.application.ports import UnitOfWork
from qi_flow.domain.models import DeductionKind
from qi_flow.domain.time_rules import COPENHAGEN


@dataclass(frozen=True, slots=True)
class DetailedExportRow:
    kind: str
    work_date: date
    started_at: datetime
    ended_at: datetime
    seconds: int


class CsvTimesheetExporter:
    """Write Danish-compatible CSV files without exposing raw timer metadata."""

    def __init__(self, uow_factory: Callable[[], UnitOfWork]) -> None:
        self._uow_factory = uow_factory

    def write_summary(self, path: Path, summaries: Iterable[DaySummaryView]) -> None:
        rows = list(summaries)
        with path.open("w", encoding="utf-8", newline="") as output:
            writer = csv.writer(output, delimiter=";")
            writer.writerow(
                [
                    "Dato",
                    "Start",
                    "Slut",
                    "Sessioner",
                    "Frokost",
                    "Pause",
                    "Netto timer",
                    "Lokation",
                    "Noter",
                ]
            )
            for summary in rows:
                with self._uow_factory() as uow:
                    details = uow.days.get(summary.work_date)
                writer.writerow(
                    [
                        summary.work_date.strftime("%d/%m/%Y"),
                        self._time(summary.first_start),
                        self._time(summary.final_finish),
                        summary.session_count,
                        self._hours(summary.lunch_seconds),
                        self._hours(summary.break_seconds),
                        self._hours(summary.net_seconds),
                        details.location.value if details else "",
                        details.note if details else "",
                    ]
                )

    def write_detailed(self, path: Path, start_date: date, end_date: date) -> None:
        rows = self._detailed_rows(start_date, end_date)
        with path.open("w", encoding="utf-8", newline="") as output:
            writer = csv.writer(output, delimiter=";")
            writer.writerow(["Type", "Dato", "Start", "Slut", "Timer"])
            for row in rows:
                writer.writerow(
                    [
                        row.kind,
                        row.work_date.strftime("%d/%m/%Y"),
                        self._time(row.started_at),
                        self._time(row.ended_at),
                        self._hours(row.seconds),
                    ]
                )

    def _detailed_rows(self, start_date: date, end_date: date) -> list[DetailedExportRow]:
        start = datetime.combine(start_date, datetime.min.time(), COPENHAGEN)
        end = datetime.combine(end_date, datetime.min.time(), COPENHAGEN)
        rows: list[DetailedExportRow] = []
        with self._uow_factory() as uow:
            for session in uow.sessions.list_intersecting(start, end):
                if session.effective_started_at is None or session.effective_ended_at is None:
                    continue
                rows.append(
                    self._row("Arbejde", session.effective_started_at, session.effective_ended_at)
                )
                for deduction in uow.deductions.list_for_session(session.id):
                    if (
                        deduction.deleted_at is not None
                        or deduction.effective_started_at is None
                        or deduction.effective_ended_at is None
                    ):
                        continue
                    kind = "Frokost" if deduction.kind is DeductionKind.LUNCH else "Pause"
                    rows.append(
                        self._row(
                            kind, deduction.effective_started_at, deduction.effective_ended_at
                        )
                    )
        return sorted(rows, key=lambda row: (row.started_at, row.kind))

    @staticmethod
    def _row(kind: str, started_at: datetime, ended_at: datetime) -> DetailedExportRow:
        local_start = started_at.astimezone(COPENHAGEN)
        return DetailedExportRow(
            kind,
            local_start.date(),
            started_at,
            ended_at,
            int((ended_at - started_at).total_seconds()),
        )

    @staticmethod
    def _time(value: datetime | None) -> str:
        return value.astimezone(COPENHAGEN).strftime("%H:%M") if value else ""

    @staticmethod
    def _hours(seconds: int) -> str:
        return f"{seconds / 3600:.2f}".replace(".", ",")
