"""Domain-facing error hierarchy."""

from __future__ import annotations


class DomainError(Exception):
    """Base class for a user-correctable domain failure."""


class InvalidIntervalError(DomainError):
    """Raised when an interval has invalid or missing boundaries."""


class OverlappingIntervalError(DomainError):
    """Raised when intervals overlap in a way the model cannot represent."""


class InvalidStateTransitionError(DomainError):
    """Raised when a timer action is not valid in the current state."""


class RecoveryRequiredError(DomainError):
    """Raised when ambiguous persisted state must be resolved first."""
