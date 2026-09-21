param(
    [switch]$InstallDependencies,
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    throw "Create the project's virtual environment first: py -m venv .venv; .\.venv\Scripts\python.exe -m pip install -e '.[dev]'"
}

if ($InstallDependencies) {
    & $python -m pip install pyinstaller
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller installation failed with exit code $LASTEXITCODE."
    }
}

$projectVersion = if ($Version) {
    $Version
} else {
    & $python -c "import tomllib; print(tomllib.load(open('pyproject.toml', 'rb'))['project']['version'])"
}
$projectVersion = $projectVersion.Trim()
$env:QI_FLOW_VERSION = $projectVersion

# Build with Windows' persistent PATH entries instead of inheriting paths injected
# by the invoking tool.  PyInstaller scans PATH for DLL dependencies; a host tool's
# ICU libraries can otherwise be copied into the app and prevent Qt from loading.
$buildPathEntries = @(
    "$projectRoot\.venv\Scripts",
    [Environment]::GetEnvironmentVariable("Path", "Machine"),
    [Environment]::GetEnvironmentVariable("Path", "User")
) | Where-Object { $_ }
$env:PATH = $buildPathEntries -join ";"

# Fail before packaging if the virtual environment cannot load Qt with the same
# DLL search path used by PyInstaller. This prevents a misleading installer
# build when the development machine itself has incompatible native libraries.
& $python -c "from PySide6.QtCore import qVersion; print('Qt ' + qVersion())"
if ($LASTEXITCODE -ne 0) {
    throw "Qt could not be imported from the build environment. The installer was not created."
}

# PyInstaller leaves stale files in an existing one-folder distribution. A partial build could
# otherwise be packaged with incompatible Qt/Python files from an earlier build.
$bundle = Join-Path $projectRoot "dist\QI Flow"
$buildOutput = Join-Path $projectRoot "build\QI Flow"
foreach ($output in @($bundle, $buildOutput)) {
    if (Test-Path $output) {
        $resolved = (Resolve-Path $output).Path
        if (-not $resolved.StartsWith($projectRoot, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to remove build output outside the project: $resolved"
        }
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}

Push-Location $projectRoot
try {
    & $python scripts/generate-icon.py
    if ($LASTEXITCODE -ne 0) {
        throw "Icon generation failed with exit code $LASTEXITCODE."
    }
    & $python -m PyInstaller --noconfirm --clean --windowed --name "QI Flow" `
        --icon "src\qi_flow\assets\qiflow-icon.ico" `
        --collect-all playwright `
        --collect-all keyring `
        --collect-all win32ctypes `
        --collect-all googleapiclient `
        --collect-all google_auth_oauthlib `
        --collect-all google.auth `
        --collect-all google.oauth2 `
        --hidden-import google_auth_httplib2 `
        --hidden-import tzdata `
        --add-data "src\qi_flow\infrastructure\sqlite\migrations;qi_flow\infrastructure\sqlite\migrations" `
        --add-data "src\qi_flow\assets;qi_flow\assets" `
        --paths src `
        src/qi_flow/__main__.py
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE."
    }
    $requiredBundleFiles = @(
        (Join-Path $bundle "QI Flow.exe"),
        (Join-Path $bundle "_internal\base_library.zip")
    )
    foreach ($file in $requiredBundleFiles) {
        if (-not (Test-Path $file)) {
            throw "PyInstaller produced an incomplete application bundle; missing $file."
        }
    }
    $unexpectedIcuFiles = Get-ChildItem -Path (Join-Path $bundle "_internal") -Filter "icu*.dll" -File
    if ($unexpectedIcuFiles) {
        $names = ($unexpectedIcuFiles | ForEach-Object Name) -join ", "
        throw "PyInstaller copied unexpected ICU DLLs ($names). Rebuild from a clean environment; the installer was not created."
    }
    & (Join-Path $bundle "QI Flow.exe") --smoke-check
    if ($LASTEXITCODE -ne 0) {
        throw "The packaged QI Flow smoke check failed; the installer was not created."
    }
} finally {
    Pop-Location
}

$iscc = @(
    (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source,
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1

if (-not $iscc) {
    throw "Inno Setup 6 is required to create the installer. Install it, then rerun this script."
}

& $iscc "/DMyAppVersion=$projectVersion" "installer\QIFlow.iss"
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup failed with exit code $LASTEXITCODE."
}

Write-Host "Installer created in dist/installer. It installs per user and preserves local data on uninstall."
