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
