param([string]$Python = "")

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$pythonCommand = if ($Python) { $Python } elseif (Test-Path $venvPython) { $venvPython } else { "python" }

function Invoke-PythonCheck {
    param([string[]]$CommandArguments)

    & $pythonCommand @CommandArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Check failed: python $($CommandArguments -join ' ')"
    }
}

Push-Location $projectRoot
$previousPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = Join-Path $projectRoot "src"
try {
    Invoke-PythonCheck -CommandArguments @("-m", "ruff", "format", "--check", "src", "tests", "scripts")
    Invoke-PythonCheck -CommandArguments @("-m", "ruff", "check", "src", "tests", "scripts")
    Invoke-PythonCheck -CommandArguments @("-m", "mypy")
    Invoke-PythonCheck -CommandArguments @("-m", "pytest", "--basetemp=.pytest-tmp")
}
finally {
    $env:PYTHONPATH = $previousPythonPath
    Pop-Location
}
