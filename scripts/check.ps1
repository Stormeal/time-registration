$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$pythonCommand = if (Test-Path $venvPython) { $venvPython } else { "python" }

function Invoke-PythonCheck {
    param([string[]]$CommandArguments)

    & $pythonCommand @CommandArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Check failed: python $($CommandArguments -join ' ')"
    }
}

Push-Location $projectRoot
try {
    Invoke-PythonCheck -CommandArguments @("-m", "ruff", "format", "--check", "src", "tests")
    Invoke-PythonCheck -CommandArguments @("-m", "ruff", "check", "src", "tests")
    Invoke-PythonCheck -CommandArguments @("-m", "mypy")
    Invoke-PythonCheck -CommandArguments @("-m", "pytest", "--basetemp=.pytest-tmp")
}
finally {
    Pop-Location
}
