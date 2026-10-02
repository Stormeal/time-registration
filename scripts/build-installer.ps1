param(
    [switch]$InstallDependencies,
    [string]$Version = "",
    [string]$OutputRoot = ""
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

$projectVersion = & $python -c "import tomllib; print(tomllib.load(open('pyproject.toml', 'rb'))['project']['version'])"
$projectVersion = $projectVersion.Trim()
$runtimeVersion = & $python -c "import qi_flow; print(qi_flow.__version__)"
if ($LASTEXITCODE -ne 0 -or $runtimeVersion.Trim() -ne $projectVersion) {
    throw "The installed QI Flow package version does not match pyproject.toml. Reinstall the project before packaging."
}
if ($Version -and $Version -ne $projectVersion) {
    throw "The requested installer version must match the QI Flow package version ($projectVersion)."
}

# Build with Windows' persistent PATH entries instead of inheriting paths injected
# by the invoking tool.  PyInstaller scans PATH for DLL dependencies; a host tool's
# ICU libraries can otherwise be copied into the app and prevent Qt from loading.
$machinePathEntries = [Environment]::GetEnvironmentVariable("Path", "Machine") -split ";"
$userPathEntries = [Environment]::GetEnvironmentVariable("Path", "User") -split ";"
$buildPathEntries = @("$projectRoot\.venv\Scripts") + $machinePathEntries + $userPathEntries
$buildPathEntries = $buildPathEntries | Where-Object {
    $_ -and
    -not $_.ToLowerInvariant().Contains("\.cache\codex-runtimes\") -and
    -not $_.ToLowerInvariant().Contains("\.codex\")
}
$env:PATH = $buildPathEntries -join ";"

# Fail before packaging if the virtual environment cannot load Qt with the same
# DLL search path used by PyInstaller. This prevents a misleading installer
# build when the development machine itself has incompatible native libraries.
& $python -c "from PySide6.QtCore import qVersion; print('Qt ' + qVersion())"
if ($LASTEXITCODE -ne 0) {
    throw "Qt could not be imported from the build environment. The installer was not created."
}

# A caller-supplied output root allows isolated packaging checks without touching the normal
# dist/build output. PyInstaller leaves stale files in an existing one-folder distribution. A partial build could
# otherwise be packaged with incompatible Qt/Python files from an earlier build.
$artifactRoot = if ($OutputRoot) {
    [System.IO.Path]::GetFullPath($OutputRoot)
} else {
    Join-Path $projectRoot "dist"
}
$workRoot = if ($OutputRoot) {
    Join-Path $artifactRoot "build"
} else {
    Join-Path $projectRoot "build"
}
$bundle = Join-Path $artifactRoot "QI Flow"
$buildOutput = Join-Path $workRoot "QI Flow"
foreach ($output in @(
    @{ Path = $bundle; Root = $artifactRoot },
    @{ Path = $buildOutput; Root = $workRoot }
)) {
    $outputPath = $output.Path
    $outputRootPath = [System.IO.Path]::GetFullPath($output.Root).TrimEnd('\') + '\'
    if (Test-Path $outputPath) {
        $resolved = (Resolve-Path $outputPath).Path
        if (-not $resolved.StartsWith($outputRootPath, [StringComparison]::OrdinalIgnoreCase)) {
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
        --distpath $artifactRoot --workpath $buildOutput --specpath $workRoot `
        --icon (Join-Path $projectRoot "src\qi_flow\assets\qiflow-icon.ico") `
        --collect-all playwright `
        --collect-all keyring `
        --collect-all win32ctypes `
        --collect-all googleapiclient `
        --collect-all google_auth_oauthlib `
        --collect-all google.auth `
        --collect-all google.oauth2 `
        --hidden-import google_auth_httplib2 `
        --hidden-import tzdata `
        --add-data "$(Join-Path $projectRoot 'src\qi_flow\infrastructure\sqlite\migrations');qi_flow\infrastructure\sqlite\migrations" `
        --add-data "$(Join-Path $projectRoot 'src\qi_flow\assets');qi_flow\assets" `
        --paths (Join-Path $projectRoot "src") `
        src/qi_flow/__main__.py
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE."
    }
    & $python scripts/filter_host_runtime_dlls.py `
        --analysis (Join-Path $buildOutput "QI Flow\COLLECT-00.toc") --bundle $bundle
    if ($LASTEXITCODE -ne 0) {
        throw "Host-injected runtime DLLs could not be filtered from the application bundle."
    }
    & $python -m PyInstaller --noconfirm --clean --onefile --windowed `
        --distpath $artifactRoot --workpath (Join-Path $workRoot "updater") --specpath $workRoot `
        --name "QI Flow Updater" scripts/update_helper.py
    if ($LASTEXITCODE -ne 0) {
        throw "The QI Flow updater helper build failed with exit code $LASTEXITCODE."
    }
    Copy-Item -LiteralPath (Join-Path $artifactRoot "QI Flow Updater.exe") `
        -Destination (Join-Path $bundle "QI Flow Updater.exe")
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
    & $python scripts/build-update-package.py --bundle-dir $bundle --output-dir $artifactRoot
    if ($LASTEXITCODE -ne 0) {
        throw "The in-app update package could not be assembled."
    }
} finally {
    Pop-Location
}

$iscc = @(
    (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source,
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1

if (-not $iscc) {
    throw "Inno Setup 6 is required to create the installer. Install it, then rerun this script."
}

$installerOutput = Join-Path $artifactRoot "installer"
& $iscc "/DMyAppVersion=$projectVersion" "/DMyAppBundleDir=$bundle" `
    "/DMyAppOutputDir=$installerOutput" "installer\QIFlow.iss"
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup failed with exit code $LASTEXITCODE."
}

Write-Host "Installer created in $installerOutput. It installs per user and preserves local data on uninstall."
