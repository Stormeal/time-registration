from __future__ import annotations

from pathlib import Path

import pytest
from scripts.release_version import resolve_release_version, stamp_release_version


def test_explicit_version_must_be_newer_than_project_and_tags() -> None:
    assert resolve_release_version("0.3.0", "0.2.5", ["v0.2.8"]) == "0.3.0"


def test_default_version_increments_patch_from_greatest_tag() -> None:
    assert resolve_release_version(None, "0.2.5", ["v0.2.3", "v0.2.9"]) == "0.2.10"


def test_default_version_falls_back_to_project_version_without_tags() -> None:
    assert resolve_release_version(None, "0.2.5", ["legacy", "v1.2-beta"]) == "0.2.6"


def test_default_version_uses_project_version_when_it_is_ahead_of_tags() -> None:
    assert resolve_release_version(None, "0.4.2", ["v0.4.0", "v0.3.9"]) == "0.4.3"


@pytest.mark.parametrize("requested", ["v0.3.0", "0.3", "0.3.0-beta", " 0.3.0"])
def test_malformed_explicit_version_is_rejected(requested: str) -> None:
    with pytest.raises(ValueError, match=r"MAJOR.MINOR.PATCH"):
        resolve_release_version(requested, "0.2.5", ["v0.2.6"])


@pytest.mark.parametrize(
    ("requested", "project_version", "tags"),
    [
        ("0.2.6", "0.2.5", ["v0.2.6"]),
        ("0.2.4", "0.2.5", ["v0.2.3"]),
        ("0.2.8", "0.2.5", ["v0.2.9"]),
    ],
)
def test_explicit_version_must_be_newer_than_current_versions(
    requested: str, project_version: str, tags: list[str]
) -> None:
    with pytest.raises(ValueError, match="newer"):
        resolve_release_version(requested, project_version, tags)


def test_stamp_updates_only_the_two_version_declarations(tmp_path: Path) -> None:
    project = tmp_path / "pyproject.toml"
    package = tmp_path / "src" / "qi_flow" / "__init__.py"
    package.parent.mkdir(parents=True)
    project.write_text('[project]\nname = "qi-flow"\nversion = "0.2.5"\n', encoding="utf-8")
    package.write_text('"""QI Flow."""\n__version__ = "0.2.5"\n', encoding="utf-8")

    stamp_release_version(tmp_path, "0.2.6")

    assert project.read_text(encoding="utf-8") == (
        '[project]\nname = "qi-flow"\nversion = "0.2.6"\n'
    )
    assert package.read_text(encoding="utf-8") == '"""QI Flow."""\n__version__ = "0.2.6"\n'


def test_stamp_rejects_malformed_version_without_changing_files(tmp_path: Path) -> None:
    project = tmp_path / "pyproject.toml"
    package = tmp_path / "src" / "qi_flow" / "__init__.py"
    package.parent.mkdir(parents=True)
    original_project = '[project]\nversion = "0.2.5"\n'
    original_package = '__version__ = "0.2.5"\n'
    project.write_text(original_project, encoding="utf-8")
    package.write_text(original_package, encoding="utf-8")

    with pytest.raises(ValueError, match=r"MAJOR.MINOR.PATCH"):
        stamp_release_version(tmp_path, "v0.2.6")

    assert project.read_text(encoding="utf-8") == original_project
    assert package.read_text(encoding="utf-8") == original_package


def test_stamp_rejects_missing_runtime_version_without_partial_edit(tmp_path: Path) -> None:
    project = tmp_path / "pyproject.toml"
    package = tmp_path / "src" / "qi_flow" / "__init__.py"
    package.parent.mkdir(parents=True)
    original_project = '[project]\nversion = "0.2.5"\n'
    project.write_text(original_project, encoding="utf-8")
    package.write_text('"""Missing the runtime declaration."""\n', encoding="utf-8")

    with pytest.raises(ValueError, match="runtime version"):
        stamp_release_version(tmp_path, "0.2.6")

    assert project.read_text(encoding="utf-8") == original_project
