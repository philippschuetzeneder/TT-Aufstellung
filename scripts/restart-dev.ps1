# Kill the local dev server on PORT and start app.web again (background).
$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

. (Join-Path $PSScriptRoot "load-env.ps1")

if (-not $env:PORT) { $env:PORT = "10000" }
$port = [int]$env:PORT

$pids = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique
foreach ($p in $pids) {
    Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 2

$venvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    throw "Virtual environment missing. Run .\scripts\setup-local.ps1 first."
}
if (-not $env:PYTHONPATH) { $env:PYTHONPATH = "backend" }

Write-Host "Restarting server on http://localhost:$port" -ForegroundColor Cyan
Start-Process -FilePath $venvPython -ArgumentList "-m", "app.web" -WorkingDirectory $Root -WindowStyle Hidden
