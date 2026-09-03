param([ValidateSet("uv", "pip")][string]$Method = "uv", [string]$Python = "python")
$ErrorActionPreference = "Stop"
Push-Location (Split-Path -Parent $PSScriptRoot)
try {
    if ($Method -eq "uv") {
        if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
            throw "Install uv first, or use -Method pip -Python <Python 3.10-3.13 executable>."
        }
        & uv sync --locked
        if ($LASTEXITCODE -ne 0) { throw "uv sync failed" }
    } else {
        & $Python -c "import sys; assert (3,10) <= sys.version_info[:2] < (3,14), 'Python 3.10-3.13 required'"
        if ($LASTEXITCODE -ne 0) { throw "Unsupported Python" }
        & $Python -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw "venv creation failed" }
        & .\.venv\Scripts\python.exe -m pip install -r requirements.txt
        if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed" }
    }
    Write-Host "Ready. Run: uv run --no-sync python run.py OR .\.venv\Scripts\python.exe run.py"
} finally { Pop-Location }
