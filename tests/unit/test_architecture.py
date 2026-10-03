"""Recursive dependency guards, including relative and dynamic import boundaries."""

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path("src")


def violations(path: Path, root: Path = ROOT) -> list[str]:
    parts = path.relative_to(root).with_suffix("").parts
    module = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
    package = module if parts[-1] == "__init__" else module.rpartition(".")[0]
    layer = parts[1]
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports = []
    errors = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            name = importlib.util.resolve_name("." * node.level + (node.module or ""), package)
            imports.append(name)
            imports.extend(name + "." + alias.name for alias in node.names)
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                if (
                    node.func.attr == "import_module"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                ):
                    imports.append(str(node.args[0].value))
                if layer == "ui" and node.func.attr in {
                    "execute",
                    "executemany",
                    "executescript",
                    "sync_for",
                }:
                    errors.append("UI database access")
        elif layer == "ui" and isinstance(node, ast.Attribute) and node.attr == "_uow_factory":
            errors.append("UI private repository access")
    for name in imports:
        first = name.split(".")[0]
        if (
            layer == "domain"
            and first not in sys.stdlib_module_names
            and not name.startswith("qi_flow.domain")
        ):
            errors.append(name)
        if (
            layer == "application"
            and first not in sys.stdlib_module_names
            and not (name.startswith("qi_flow.application") or name.startswith("qi_flow.domain"))
        ):
            errors.append(name)
        if layer == "infrastructure" and name.startswith("qi_flow.ui"):
            errors.append(name)
        if layer == "ui" and name.startswith("qi_flow.infrastructure"):
            errors.append(name)
    return errors


@pytest.mark.parametrize("layer", ["domain", "application", "infrastructure", "ui"])
def test_inward_dependencies_recursively(layer):
    failures = {
        str(path): found
        for path in (ROOT / "qi_flow" / layer).rglob("*.py")
        if (found := violations(path))
    }
    assert not failures, failures


@pytest.mark.parametrize(
    "layer,content",
    [
        ("domain", "import requests"),
        ("application", "from ...infrastructure.sqlite import database"),
        ("application", 'import importlib\nimportlib.import_module("PySide6.QtCore")'),
        ("infrastructure", "from ...ui import main_window"),
        ("ui", 'def action(service):\n    service._uow_factory().execute("SELECT 1")'),
    ],
)
def test_guard_detects_nested_relative_and_dynamic_violations(tmp_path, layer, content):
    path = tmp_path / "qi_flow" / layer / "nested" / "module.py"
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    assert violations(path, tmp_path)
