"""Module entry point for ``python -m qi_flow``."""

from __future__ import annotations

import importlib
import sys


def main(argv: list[str] | None = None) -> int:
    """Start the QI Flow desktop application."""
    arguments = list(argv if argv is not None else sys.argv[1:])
    if "--smoke-check" in arguments:
        importlib.import_module("PySide6.QtCore")
        importlib.import_module("google_auth_oauthlib.flow")
        importlib.import_module("googleapiclient.discovery")
        return 0
    from qi_flow.bootstrap import run

    return run(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
