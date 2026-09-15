"""Tests for invariants owned by domain entities."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from qi_flow.domain.errors import InvalidIntervalError
from qi_flow.domain.models import SessionId, WorkSession


def test_work_session_requires_timezone_aware_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        WorkSession(SessionId("session-1"), datetime(2026, 9, 15, 8, 0))


def test_work_session_rejects_non_positive_duration() -> None:
    start = datetime(2026, 9, 15, 8, 0, tzinfo=UTC)

    with pytest.raises(InvalidIntervalError, match="after its start"):
        WorkSession(SessionId("session-1"), start, start - timedelta(minutes=1))


def test_work_session_reports_active_state() -> None:
    session = WorkSession(
        SessionId("session-1"),
        datetime(2026, 9, 15, 8, 0, tzinfo=UTC),
    )

    assert session.is_active
