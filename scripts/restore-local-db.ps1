# Restore tt_aufstellung_render.dump into local PostgreSQL (local only).
param(
    [string]$DumpFile = (Join-Path (Split-Path $PSScriptRoot -Parent) "tt_aufstellung_render.dump")
)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

. (Join-Path $PSScriptRoot "load-env.ps1")

if (-not (Test-Path $DumpFile)) { throw "Missing dump file: $DumpFile" }
if ((Get-Item $DumpFile).Length -lt 1024) { throw "Dump file is empty or invalid." }

function Find-PgTool($name) {
    foreach ($ver in @("18", "17")) {
        $candidate = "C:\Program Files\PostgreSQL\$ver\bin\$name.exe"
        if (Test-Path $candidate) { return $candidate }
    }
    throw "PostgreSQL tool '$name' not found."
}

$pgRestore = Find-PgTool "pg_restore"
$psql = Find-PgTool "psql"

$localUrl = $env:DATABASE_URL
if (-not $localUrl) { $localUrl = "postgresql+psycopg://tt:tt_dev@127.0.0.1:5432/tt_aufstellung" }
$localPg = $localUrl -replace "^postgresql\+psycopg2?://", "postgresql://"
if ($localPg -match "@localhost") { $localPg = $localPg -replace "@localhost", "@127.0.0.1" }

$superPass = $env:POSTGRES_SUPERUSER_PASSWORD
$adminUri = if ($superPass) {
    "postgresql://postgres:$superPass@127.0.0.1:5432/postgres"
} else {
    "postgresql://postgres@127.0.0.1:5432/postgres"
}

Write-Host "Creating local role/database if needed ..." -ForegroundColor Cyan
& $psql $adminUri -v ON_ERROR_STOP=1 -c "DO `$`$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'tt') THEN CREATE ROLE tt LOGIN PASSWORD 'tt_dev'; END IF; END `$`$;"
$dbExists = (& $psql $adminUri -tAc "SELECT 1 FROM pg_database WHERE datname = 'tt_aufstellung'").Trim()
if ($dbExists -ne "1") {
    & $psql $adminUri -v ON_ERROR_STOP=1 -c "CREATE DATABASE tt_aufstellung OWNER tt"
}

Write-Host "Resetting local schema ..." -ForegroundColor Yellow
& $psql $localPg -v ON_ERROR_STOP=1 -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"

Write-Host "Restoring dump ..." -ForegroundColor Cyan
& $pgRestore --dbname=$localPg --no-owner --no-acl $DumpFile

Write-Host "Row counts:" -ForegroundColor Green
& $psql $localPg -c @"
SELECT 'xttv_matches' AS table, COUNT(*)::text AS count FROM xttv_matches
UNION ALL SELECT 'xttv_players', COUNT(*)::text FROM xttv_players
UNION ALL SELECT 'match_players', COUNT(*)::text FROM match_players;
"@
