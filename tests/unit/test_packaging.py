"""Fast release checks for the packaged application's critical imports."""

from __future__ import annotations

from pathlib import Path

from qi_flow.__main__ import main


def test_smoke_check_imports_qt_and_google_sync_dependencies() -> None:
    assert main(["--smoke-check"]) == 0


def test_installer_build_refuses_incomplete_bundle_and_runs_smoke_check() -> None:
    script = Path("scripts/build-installer.ps1").read_text(encoding="utf-8")

    assert "--collect-all google_auth_oauthlib" in script
    assert "--collect-all googleapiclient" in script
    assert "from PySide6.QtCore import qVersion" in script
    assert "Remove-Item -LiteralPath $resolved -Recurse -Force" in script
    assert "_internal\\base_library.zip" in script
    assert 'Filter "icu*.dll" -File' in script
    assert '"QI Flow.exe") --smoke-check' in script
