param([ValidateSet("all", "installer", "portable", "edge")][string]$Target = "all")
$ErrorActionPreference = "Stop"
$previousBuildEnvironment = $env:UV_PROJECT_ENVIRONMENT
Push-Location (Split-Path -Parent $PSScriptRoot)
try {
    $env:UV_PROJECT_ENVIRONMENT = "build/release-venv"
    & uv sync --locked --group release
    if ($LASTEXITCODE -ne 0) { throw "Build environment setup failed" }
    & uv run --no-sync python scripts/build_release.py --target $Target
    if ($LASTEXITCODE -ne 0) { throw "Release build failed" }
} finally {
    $env:UV_PROJECT_ENVIRONMENT = $previousBuildEnvironment
    Pop-Location
}
