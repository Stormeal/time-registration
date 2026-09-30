"""Resolve and stamp QI Flow's version for a runner-only release build."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Iterable
from pathlib import Path

_VERSION_PATTERN = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_PROJECT_TABLE_PATTERN = re.compile(r"(?ms)^\[project\][ \t]*\r?\n(?P<body>.*?)(?=^\[|\Z)")
_PROJECT_VERSION_PATTERN = re.compile(
    r'(?m)^(?P<prefix>[ \t]*version[ \t]*=[ \t]*")(?P<value>[^"]+)(?P<suffix>"[ \t]*\r?)$'
)
_RUNTIME_VERSION_PATTERN = re.compile(
    r'(?m)^(?P<prefix>__version__[ \t]*=[ \t]*")(?P<value>[^"]+)(?P<suffix>")[ \t]*\r?$'
)


def _parse_version(value: str) -> tuple[int, int, int]:
    match = _VERSION_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError(f"Version must use MAJOR.MINOR.PATCH, without a leading v: {value!r}.")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def resolve_release_version(
    requested: str | None, project_version: str, tags: Iterable[str]
) -> str:
    """Return the validated explicit version or the next available patch version."""
    project_tuple = _parse_version(project_version)
    tagged_versions = [
        _parse_version(tag[1:])
        for tag in tags
        if tag.startswith("v") and _VERSION_PATTERN.fullmatch(tag[1:]) is not None
    ]
    latest_tuple = max([project_tuple, *tagged_versions])

    if requested is not None and requested != "":
        requested_tuple = _parse_version(requested)
        if requested_tuple <= latest_tuple:
            raise ValueError(
                "The requested version must be newer than the project version and all version tags."
            )
        return requested

    major, minor, patch = latest_tuple
    return f"{major}.{minor}.{patch + 1}"


def _prepare_project_version(content: str, version: str) -> str:
    table_match = _PROJECT_TABLE_PATTERN.search(content)
    if table_match is None:
        raise ValueError("Could not find the [project] table in pyproject.toml.")
    body = table_match.group("body")
    matches = list(_PROJECT_VERSION_PATTERN.finditer(body))
    if len(matches) != 1:
        raise ValueError("Could not find exactly one project version in pyproject.toml.")
    updated_body = _PROJECT_VERSION_PATTERN.sub(
        lambda match: f"{match.group('prefix')}{version}{match.group('suffix')}", body, count=1
    )
    return content[: table_match.start("body")] + updated_body + content[table_match.end("body") :]


def _prepare_runtime_version(content: str, version: str) -> str:
    matches = list(_RUNTIME_VERSION_PATTERN.finditer(content))
    if len(matches) != 1:
        raise ValueError("Could not find exactly one runtime version in src/qi_flow/__init__.py.")
    return _RUNTIME_VERSION_PATTERN.sub(
        lambda match: f"{match.group('prefix')}{version}{match.group('suffix')}",
        content,
        count=1,
    )


def _replace_files_atomically(contents: dict[Path, bytes]) -> None:
    originals = {path: path.read_bytes() for path in contents}
    temporary: dict[Path, Path] = {}
    replaced: list[Path] = []
    try:
        for path, data in contents.items():
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
            ) as handle:
                handle.write(data)
                temporary[path] = Path(handle.name)
        for path, temp_path in temporary.items():
            os.replace(temp_path, path)
            replaced.append(path)
    except OSError:
        for path in replaced:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".rollback",
                delete=False,
            ) as handle:
                handle.write(originals[path])
                rollback_path = Path(handle.name)
            os.replace(rollback_path, path)
        raise
    finally:
        for temp_path in temporary.values():
            temp_path.unlink(missing_ok=True)


def stamp_release_version(project_root: Path, version: str) -> None:
    """Update project/runtime versions in the disposable release-build checkout."""
    _parse_version(version)
    project_path = project_root / "pyproject.toml"
    runtime_path = project_root / "src" / "qi_flow" / "__init__.py"
    try:
        project_content = project_path.read_bytes().decode("utf-8")
        runtime_content = runtime_path.read_bytes().decode("utf-8")
    except OSError as error:
        raise ValueError(f"Could not read a version file: {error}") from error

    updated_project = _prepare_project_version(project_content, version)
    updated_runtime = _prepare_runtime_version(runtime_content, version)
    try:
        tomllib.loads(updated_project)
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"The stamped pyproject.toml is invalid: {error}") from error

    try:
        _replace_files_atomically(
            {
                project_path: updated_project.encode("utf-8"),
                runtime_path: updated_runtime.encode("utf-8"),
            }
        )
    except OSError as error:
        raise ValueError(f"Could not write stamped version files: {error}") from error


def _project_version(project_root: Path) -> str:
    try:
        with (project_root / "pyproject.toml").open("rb") as project_file:
            metadata = tomllib.load(project_file)
        version = metadata["project"]["version"]
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError) as error:
        raise ValueError(f"Could not read project version from pyproject.toml: {error}") from error
    if not isinstance(version, str):
        raise ValueError("The project version in pyproject.toml must be a string.")
    return version


def _git_tags(project_root: Path) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "tag", "--list"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise ValueError(f"Could not read repository version tags: {error}") from error
    return result.stdout.splitlines()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    resolve_parser = subparsers.add_parser("resolve", help="select a validated release version")
    resolve_parser.add_argument("--requested", default="", help="optional MAJOR.MINOR.PATCH")
    stamp_parser = subparsers.add_parser("stamp", help="stamp version files in this checkout")
    stamp_parser.add_argument("--version", required=True, help="MAJOR.MINOR.PATCH")
    args = parser.parse_args(argv)
    project_root = Path(__file__).resolve().parents[1]

    try:
        if args.command == "resolve":
            version = resolve_release_version(
                args.requested or None, _project_version(project_root), _git_tags(project_root)
            )
            print(version)
        else:
            stamp_release_version(project_root, args.version)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
