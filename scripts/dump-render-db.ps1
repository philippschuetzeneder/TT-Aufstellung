# Read-only dump from Render PostgreSQL (does not modify Render or local DB).
param(
    [string]$RenderUrl = $env:RENDER_DATABASE_URL,
    [string]$DumpFile = (Join-Path (Split-Path $PSScriptRoot -Parent) "tt_aufstellung_render.dump")
)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

. (Join-Path $PSScriptRoot "load-env.ps1")
if (-not $RenderUrl) { $RenderUrl = $env:RENDER_DATABASE_URL }
if (-not $RenderUrl) { throw "RENDER_DATABASE_URL is not set in .env" }

function Find-PgTool($name) {
    foreach ($ver in @("18", "17")) {
        $candidate = "C:\Program Files\PostgreSQL\$ver\bin\$name.exe"
        if (Test-Path $candidate) { return $candidate }
    }
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    throw "PostgreSQL tool '$name' not found."
}

$pgDump = Find-PgTool "pg_dump"
Write-Host "Dumping Render (read-only) with $pgDump ..." -ForegroundColor Cyan
& $pgDump $RenderUrl --no-owner --no-acl --format=custom -f $DumpFile
$size = (Get-Item $DumpFile).Length
Write-Host "Dump written: $DumpFile ($size bytes)" -ForegroundColor Green
if ($size -lt 1024) { throw "Dump too small - dump likely failed." }
