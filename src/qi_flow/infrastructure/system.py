"""Standard implementations for process-local infrastructure ports."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from qi_flow.domain.models import DeductionId, SessionId


class SystemClock:
    """Return timezone-aware UTC instants."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class UuidIdentifierGenerator:
    """Generate UUID-based entity identifiers."""

    def session_id(self) -> SessionId:
        return SessionId(str(uuid4()))

    def deduction_id(self) -> DeductionId:
        return DeductionId(str(uuid4()))

    def audit_id(self) -> str:
        return str(uuid4())
