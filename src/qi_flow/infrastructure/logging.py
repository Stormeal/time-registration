"""Privacy-conscious local diagnostic logging."""

from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from qi_flow import __version__

_BROWSER_STAGES = frozenset(
    {
        "starting Microsoft Edge",
        "creating the temporary browser session",
        "opening the Testhuset login page",
        "reading the Testhuset weekly sheet",
    }
)
_ERROR_CATEGORIES = frozenset(
    {
        "AssertionError",
        "BrowserError",
        "CredentialManagerError",
        "DependencyError",
        "Error",
        "ImportError",
        "NetworkError",
        "OSError",
        "PermissionError",
        "TimeoutError",
        "UnexpectedError",
        "ValueError",
    }
)


def _safe_category(value: object) -> str:
    return value if type(value) is str and value in _ERROR_CATEGORIES else "UnexpectedError"


def _approved_message(record: logging.LogRecord) -> str | None:
    """Return only known application events with bounded, non-sensitive fields."""
    name, message, args = record.name, record.msg, record.args
    if type(message) is not str:
        return None
    if name == "qi_flow.bootstrap":
        if (
            message
            in {
                "Another QI Flow instance is already running; it was asked to focus itself",
                "System tray unavailable; using window lifecycle",
            }
            and not args
        ):
            return message
        if message == "QI Flow %s started; data directory initialized" and args == (__version__,):
            return f"QI Flow {__version__} started; data directory initialized"
        if (
            message == "QI Flow stopped with exit code %d"
            and type(args) is tuple
            and len(args) == 1
            and type(args[0]) is int
        ):
            return f"QI Flow stopped with exit code {args[0]}"
    if (
        name == "qi_flow.infrastructure.google_oauth"
        and message == "Google authorization failed (%s)"
        and type(args) is tuple
        and len(args) == 1
    ):
        return f"Google authorization failed ({_safe_category(args[0])})"
    if (
        name == "qi_flow.infrastructure.testhuset_browser"
        and message == "Testhuset browser failure during %s (%s)"
        and type(args) is tuple
        and len(args) == 2
    ):
        stage = args[0] if type(args[0]) is str and args[0] in _BROWSER_STAGES else "unknown stage"
        return f"Testhuset browser failure during {stage} ({_safe_category(args[1])})"
    if (
        name == "qi_flow.ui.testhuset_dialog"
        and message == "%s operation failed (%s)"
        and type(args) is tuple
        and len(args) == 2
    ):
        destination = (
            args[0]
            if type(args[0]) is str and args[0] in {"Testhuset", "DSB"}
            else "External service"
        )
        return f"{destination} operation failed ({_safe_category(args[1])})"
    return None


class _ApplicationEventsOnly(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = _approved_message(record)
        if message is None:
            return False
        record.msg = message
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


def configure_logging(log_dir: Path) -> None:
    """Persist only approved events in a daily local log with seven retained files."""
    log_dir.mkdir(parents=True, exist_ok=True)
    root_logger = logging.getLogger()
    for previous in root_logger.handlers[:]:
        root_logger.removeHandler(previous)
        if getattr(previous, "_qi_flow_diagnostic_handler", False):
            previous.close()

    handler = TimedRotatingFileHandler(
        log_dir / "qi-flow.log",
        when="midnight",
        backupCount=7,
        encoding="utf-8",
        utc=True,
    )
    handler._qi_flow_diagnostic_handler = True  # type: ignore[attr-defined]
    handler.addFilter(_ApplicationEventsOnly())
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)
