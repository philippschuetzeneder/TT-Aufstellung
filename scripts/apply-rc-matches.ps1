# Apply all safe RC matches (matched + manual overrides).
# Mapping is fast; RC history import is optional and very slow (network per player).
param(
    [bool]$ImportHistory = $false,
    [int]$BatchSize = 500
)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

. (Join-Path $PSScriptRoot "load-env.ps1")

$base = "http://localhost:$($env:PORT)"
if (-not $env:PORT) { $base = "http://localhost:10000" }

$flag = if ($ImportHistory) { "1" } else { "0" }
Write-Host "RC match apply-all (import_history=$ImportHistory, batch_size=$BatchSize) ..." -ForegroundColor Cyan
$result = Invoke-RestMethod -Uri "$base/api/rc/match-apply-all?batch_size=$BatchSize&import_history=$flag" -TimeoutSec 7200
$result | ConvertTo-Json -Depth 4
Write-Host "players_with_rc_id_after: $($result.players_with_rc_id_after)" -ForegroundColor Green
