# Print RC backfill status every 60s (Ctrl+C to stop).
param([int]$IntervalSec = 60)

$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root
. (Join-Path $PSScriptRoot "load-env.ps1")
$env:PYTHONPATH = "backend"
$python = Join-Path $Root ".venv\Scripts\python.exe"
$statusFile = Join-Path $Root "data\rc_backfill_status.json"
$logFile = Join-Path $Root "data\rc_backfill.log"

while ($true) {
    $ts = Get-Date -Format "HH:mm:ss"
    Write-Host "`n=== $ts ===" -ForegroundColor Cyan
    if (Test-Path $statusFile) {
        Get-Content $statusFile -Raw | Write-Host
    } else {
        Write-Host "No status file yet (backfill not in phase 2 or not started)."
    }
    & $python (Join-Path $PSScriptRoot "rc_backfill_status.py")
    if (Test-Path $logFile) {
        Write-Host "--- log tail ---" -ForegroundColor DarkGray
        Get-Content $logFile -Tail 5
    }
    Start-Sleep -Seconds $IntervalSec
}
