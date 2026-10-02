"""Fast release checks for the packaged application's critical imports."""

from __future__ import annotations

import tomllib
from pathlib import Path

from scripts.filter_host_runtime_dlls import filter_host_runtime_dlls

from qi_flow import __version__
from qi_flow.__main__ import main


def test_smoke_check_imports_qt_and_google_sync_dependencies() -> None:
    assert main(["--smoke-check"]) == 0


def test_runtime_version_matches_project_package_version() -> None:
    with Path("pyproject.toml").open("rb") as project_file:
        assert __version__ == tomllib.load(project_file)["project"]["version"]


def test_installer_build_refuses_incomplete_bundle_and_runs_smoke_check() -> None:
    script = Path("scripts/build-installer.ps1").read_text(encoding="utf-8")

    assert "--collect-all google_auth_oauthlib" in script
    assert "--collect-all googleapiclient" in script
    assert "from PySide6.QtCore import qVersion" in script
    assert "Remove-Item -LiteralPath $resolved -Recurse -Force" in script
    assert "_internal\\base_library.zip" in script
    assert "scripts/filter_host_runtime_dlls.py" in script
    assert '"QI Flow.exe") --smoke-check' in script
    assert '--name "QI Flow Launcher" scripts/qi_flow_launcher.py' in script
    assert "--paths $projectRoot --icon (Join-Path $projectRoot " in script
    assert '"src\\qi_flow\\assets\\qiflow-icon.ico")' in script
    assert 'Copy-Item -LiteralPath (Join-Path $artifactRoot "QI Flow Launcher.exe")' not in script
    assert "scripts/build-update-package.py" in script


def test_filter_removes_only_dlls_from_codex_host_runtime(tmp_path: Path) -> None:
    bundle = tmp_path / "QI Flow"
    internal = bundle / "_internal"
    internal.mkdir(parents=True)
    (internal / "icuuc.dll").write_bytes(b"host")
    (internal / "Qt6Core.dll").write_bytes(b"app")
    analysis = tmp_path / "COLLECT-00.toc"
    analysis.write_text(
        repr(
            (
                [
                    (
                        "icuuc.dll",
                        r"C:\Users\test\.cache\codex-runtimes\poppler\icuuc.dll",
                        "BINARY",
                    ),
                    (
                        "Qt6Core.dll",
                        r"C:\project\.venv\Lib\site-packages\PySide6\Qt6Core.dll",
                        "BINARY",
                    ),
                ],
            )
        ),
        encoding="utf-8",
    )

    removed = filter_host_runtime_dlls(analysis, bundle)

    assert removed == ["icuuc.dll"]
    assert not (internal / "icuuc.dll").exists()
    assert (internal / "Qt6Core.dll").read_bytes() == b"app"


def test_update_package_contains_application_bundle_with_expected_layout(tmp_path) -> None:
    import runpy
    import zipfile

    root = tmp_path
    bundle = root / "dist" / "QI Flow"
    bundle.mkdir(parents=True)
    (bundle / "QI Flow.exe").write_bytes(b"app")
    (root / "dist" / "QI Flow Launcher.exe").write_bytes(b"launcher")
    (root / "scripts").mkdir()
    script_path = Path("scripts/build-update-package.py")
    script = script_path.read_text(encoding="utf-8")
    script_path = root / "scripts" / script_path.name
    script_path.write_text(script, encoding="utf-8")
    namespace = runpy.run_path(str(script_path))

    namespace["main"]([])

    with zipfile.ZipFile(root / "dist" / "QI-Flow-Update-v2.zip") as package:
        assert set(package.namelist()) == {"QI Flow/QI Flow.exe"}
    assert not (root / "dist" / "QI-Flow-Update.zip").exists()


def test_prerelease_workflow_publishes_only_v2_update_asset() -> None:
    workflow = Path(".github/workflows/prerelease.yml").read_text(encoding="utf-8")
    assert "dist/QI-Flow-Update-v2.zip" in workflow
    assert "release-assets/QI-Flow-Update-v2.zip" in workflow
    assert "dist/QI-Flow-Update.zip" not in workflow
    assert '"release-assets/QI-Flow-Update.zip"' not in workflow
    assert '--notes "$MIGRATION_NOTE"' in workflow


def test_installer_is_per_user_and_leaves_application_data_on_uninstall() -> None:
    installer = Path("installer/QIFlow.iss").read_text(encoding="utf-8")

    assert "PrivilegesRequired=lowest" in installer
    assert "PrivilegesRequiredOverridesAllowed" not in installer
    assert "{#MyAppBundleDir}" in installer
    assert "{#MyAppOutputDir}" in installer
    assert "remain in your private Windows application-data folder" in installer


def test_installer_keeps_launcher_and_uninstaller_outside_replaceable_bundle() -> None:
    installer = Path("installer/QIFlow.iss").read_text(encoding="utf-8")

    assert 'DestDir: "{app}\\current"' in installer
    assert 'DestDir: "{app}"' in installer
    assert 'Filename: "{app}\\{#MyLauncherExeName}"' in installer
    assert "UninstallDisplayIcon={app}\\{#MyLauncherExeName}" in installer
    assert 'Name: "{app}\\current"; Type: filesandordirs' in installer
    assert 'Name: "{app}\\update-work"; Type: filesandordirs' in installer
    assert "QI Flow.previous" in installer
    assert "[InstallDelete]" in installer
    assert 'Name: "{app}\\current"; Type: filesandordirs' in installer.split("[InstallDelete]")[1]
    assert (
        'Name: "{app}\\update-work"; Type: filesandordirs' in installer.split("[InstallDelete]")[1]
    )
    assert (
        'Name: "{app}\\update-transaction.json"; Type: files'
        in installer.split("[InstallDelete]")[1]
    )


def test_installer_migrates_only_its_own_startup_entry() -> None:
    installer = Path("installer/QIFlow.iss").read_text(encoding="utf-8")

    assert "RegQueryStringValue(HKCU, RunKey, 'QI Flow'" in installer
    assert "RegWriteStringValue(HKCU, RunKey, 'QI Flow'" in installer
    assert "RegDeleteValue(HKCU, RunKey, 'QI Flow'" in installer
    assert "LegacyStartupCommand" in installer
    assert "LauncherStartupCommand" in installer
    assert "RegisterExtraCloseApplicationsResources" in installer
    assert (
        "RegisterExtraCloseApplicationsResource(False, ExpandConstant('{app}\\QI Flow.exe'))"
        in installer
    )
