param([string]$Python = "")

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$defaultPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$basePython = if ($Python) { $Python } elseif (Test-Path $defaultPython) { $defaultPython } else { "python" }
$coreEnvironment = Join-Path $projectRoot ".tmp\core-check"
$corePython = Join-Path $coreEnvironment "Scripts\python.exe"

function Invoke-CoreCommand {
    param([string]$Executable, [string[]]$CommandArguments)
    & $Executable @CommandArguments
    if ($LASTEXITCODE -ne 0) { throw "Core check failed: $($CommandArguments -join ' ')" }
}

Push-Location $projectRoot
$previousPythonPath = $env:PYTHONPATH
$previousAutoload = $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD
try {
    Invoke-CoreCommand $basePython @("-m", "venv", $coreEnvironment)
    Invoke-CoreCommand $corePython @("-m", "pip", "install", "--no-deps", "-r", "requirements/core-check.txt")
    Invoke-CoreCommand $corePython @("-m", "pip", "install", "--no-deps", "--no-build-isolation", ".")
    $env:PYTHONPATH = Join-Path $projectRoot "src"
    $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = "1"
    Invoke-CoreCommand $corePython @("-c", "import importlib.util; forbidden = ['PySide6', 'playwright', 'google', 'keyring', 'pytestqt']; found = [name for name in forbidden if importlib.util.find_spec(name) is not None]; assert not found, f'Core environment contains adapters: {found}'; print('Verified: no Qt, browser, Google, keyring or Qt test plugin installed.')")
    Invoke-CoreCommand $corePython @("-m", "pytest", "-c", "pytest-core.ini", "--basetemp=.tmp/core-test-data")
}
finally {
    $env:PYTHONPATH = $previousPythonPath
    $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = $previousAutoload
    Pop-Location
}
