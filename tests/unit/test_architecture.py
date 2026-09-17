"""Guard the dependency direction that keeps business rules portable."""

from __future__ import annotations

import ast
from pathlib import Path


def test_domain_does_not_import_outer_layers() -> None:
    domain_root = Path("src/qi_flow/domain")
    forbidden = ("qi_flow.application", "qi_flow.infrastructure", "qi_flow.ui", "PySide6")

    for module_path in domain_root.glob("*.py"):
        tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)

        assert not [name for name in imports if name.startswith(forbidden)], module_path


def test_application_does_not_import_qt_browser_or_infrastructure() -> None:
    forbidden = ("qi_flow.infrastructure", "qi_flow.ui", "PySide6", "playwright")
    for module_path in Path("src/qi_flow/application").glob("*.py"):
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            assert not any(name.startswith(forbidden) for name in names), module_path
