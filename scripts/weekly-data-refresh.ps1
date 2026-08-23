# Weekly data refresh (e.g. Task Scheduler: Montag 04:00).
# Imports XTTV + RC, refreshes analysis cache, restarts the dev server.
$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

. (Join-Path $PSScriptRoot "load-env.ps1")

if (-not $env:PYTHONPATH) { $env:PYTHONPATH = "backend" }
$python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Virtual environment missing. Run .\scripts\setup-local.ps1 first."
}

Write-Host "Weekly data refresh starting ..." -ForegroundColor Cyan
& $python -c "from app.data_refresh_service import run_data_refresh; import json; print(json.dumps(run_data_refresh(restart_server=True), ensure_ascii=False, default=str))"
