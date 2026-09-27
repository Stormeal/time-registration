"""Create the one-folder update asset uploaded to a GitHub release."""

from __future__ import annotations

import sys
import argparse
import zipfile
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    output_dir = (args.output_dir or root / "dist").resolve()
    bundle = (args.bundle_dir or output_dir / "QI Flow").resolve()
    if not (bundle / "QI Flow.exe").is_file() or not (bundle / "QI Flow Updater.exe").is_file():
        raise SystemExit("Build QI Flow and QI Flow Updater before creating the update package.")
    destination = output_dir / "QI-Flow-Update.zip"
    output_dir.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as package:
        for path in bundle.rglob("*"):
            if path.is_file():
                package.write(path, Path("QI Flow") / path.relative_to(bundle))
    print(f"Created {destination}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
