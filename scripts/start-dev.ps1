# Start the local web server (backend + static frontend).
$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

. (Join-Path $PSScriptRoot "load-env.ps1")

$venvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    throw "Virtual environment missing. Run .\scripts\setup-local.ps1 first."
}

if (-not $env:PYTHONPATH) { $env:PYTHONPATH = "backend" }
if (-not $env:PORT) { $env:PORT = "10000" }

Write-Host "DATABASE_URL=$env:DATABASE_URL"
Write-Host "Starting server on http://localhost:$env:PORT" -ForegroundColor Cyan
& $venvPython -m app.web
