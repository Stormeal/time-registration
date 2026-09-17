"""Small display-formatting helpers shared across UI surfaces."""

from __future__ import annotations


def format_duration(seconds: int) -> str:
    """Render a non-negative duration as ``HH:MM:SS``."""
    hours, remainder = divmod(max(0, seconds), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}"
