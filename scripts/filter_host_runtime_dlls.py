"""Remove host-injected DLLs from a PyInstaller bundle using COLLECT provenance."""

from __future__ import annotations

import argparse
import ast
from pathlib import Path


def filter_host_runtime_dlls(analysis: Path, bundle: Path) -> list[str]:
    collected = ast.literal_eval(analysis.read_text(encoding="utf-8"))
    if not isinstance(collected, tuple) or len(collected) != 1:
        raise ValueError("PyInstaller COLLECT manifest is invalid.")

    removed: list[str] = []
    bundle_root = bundle.resolve()
    for item in collected[0]:
        if not isinstance(item, tuple) or len(item) < 3:
            continue
        destination, source, kind = item[:3]
        if kind != "BINARY" or not isinstance(source, str):
            continue
        normalized_source = source.replace("/", "\\").lower()
        if "\\.cache\\codex-runtimes\\" not in normalized_source:
            continue
        target = (bundle / "_internal" / destination).resolve()
        if not target.exists():
            target = (bundle / destination).resolve()
        if not target.is_relative_to(bundle_root):
            raise ValueError("PyInstaller manifest contains an unsafe bundle path.")
        if target.is_file():
            target.unlink()
            removed.append(str(destination))
    return removed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    arguments = parser.parse_args()
    removed = filter_host_runtime_dlls(arguments.analysis, arguments.bundle)
    print(f"Removed {len(removed)} host-injected runtime DLL(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
