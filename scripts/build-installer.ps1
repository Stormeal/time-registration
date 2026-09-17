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
        --hidden-import tzdata `
        --add-data "src\qi_flow\infrastructure\sqlite\migrations;qi_flow\infrastructure\sqlite\migrations" `
        --add-data "src\qi_flow\assets;qi_flow\assets" `
        --paths src `
        src/qi_flow/__main__.py
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE."
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
