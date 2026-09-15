"""Module entry point for ``python -m qi_flow``."""

from __future__ import annotations

from qi_flow.bootstrap import run


def main() -> int:
    """Start the QI Flow desktop application."""
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
