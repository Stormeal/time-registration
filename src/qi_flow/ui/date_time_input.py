"""Exact-minute Copenhagen input with explicit repeated-hour resolution."""

from datetime import UTC, date, datetime, time
from typing import cast

from PySide6.QtCore import QDate, QTime, Signal
from PySide6.QtWidgets import QComboBox, QDateEdit, QHBoxLayout, QTimeEdit, QWidget

from qi_flow.domain.errors import InvalidIntervalError
from qi_flow.domain.time_rules import COPENHAGEN


class DateTimeInput(QWidget):
    """Keep date/time fields independent; never infer an end-date rollover."""

    changed = Signal()

    def __init__(self, initial_date: date, label: str) -> None:
        super().__init__()
        self.date = QDateEdit(QDate(initial_date.year, initial_date.month, initial_date.day))
        self.date.setDisplayFormat("dd/MM/yyyy")
        self.date.setCalendarPopup(True)
        self.date.setAccessibleName(f"{label} date")
        self.time = QTimeEdit()
        self.time.setDisplayFormat("HH:mm")
        self.time.setAccessibleName(f"{label} time")
        self.occurrence = QComboBox()
        self.occurrence.setAccessibleName(f"{label} repeated-hour occurrence")
        self.occurrence.addItem("Choose occurrence", None)
        self.occurrence.addItem("Earlier (UTC+02:00)", "earlier")
        self.occurrence.addItem("Later (UTC+01:00)", "later")
        self.occurrence.setToolTip("This local time occurs twice. Choose which occurrence to save.")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.date)
        layout.addWidget(self.time)
        layout.addWidget(self.occurrence)
        self.date.dateChanged.connect(self._wall_time_changed)
        self.time.timeChanged.connect(self._wall_time_changed)
        self.occurrence.currentIndexChanged.connect(lambda _index: self.changed.emit())
        self._wall_time_changed()

    def _candidates(self) -> list[datetime]:
        wall = datetime.combine(
            cast(date, self.date.date().toPython()),
            time(self.time.time().hour(), self.time.time().minute()),
        )
        candidates = set()
        for fold in (0, 1):
            instant = wall.replace(tzinfo=COPENHAGEN, fold=fold).astimezone(UTC)
            if instant.astimezone(COPENHAGEN).replace(tzinfo=None) == wall:
                candidates.add(instant)
        return sorted(candidates)

    def _wall_time_changed(self) -> None:
        self.occurrence.setCurrentIndex(0)
        self.occurrence.setVisible(len(self._candidates()) == 2)
        self.changed.emit()

    def utc_value(self) -> datetime:
        candidates = self._candidates()
        if not candidates:
            raise InvalidIntervalError(
                "This Copenhagen time does not exist because the clock moves forward. "
                "Choose a valid date and time."
            )
        if len(candidates) == 1:
            return candidates[0]
        choice = self.occurrence.currentData()
        if choice is None:
            raise InvalidIntervalError("Choose the earlier or later occurrence of this time.")
        return candidates[0 if choice == "earlier" else 1]

    def set_value(self, value: datetime) -> None:
        local = value.astimezone(COPENHAGEN)
        self.date.setDate(QDate(local.year, local.month, local.day))
        self.time.setTime(QTime(local.hour, local.minute))
        candidates = self._candidates()
        if len(candidates) == 2:
            self.occurrence.setCurrentIndex(1 if local.fold == 0 else 2)

    def form_value(self) -> tuple[QDate, str, object]:
        return self.date.date(), self.time.time().toString("HH:mm"), self.occurrence.currentData()
