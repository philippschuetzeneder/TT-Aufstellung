# Read-only clone of the Render PostgreSQL database into local Postgres.
# Does NOT modify the Render database.
param(
    [string]$RenderUrl = $env:RENDER_DATABASE_URL,
    [string]$DumpFile = (Join-Path (Split-Path $PSScriptRoot -Parent) "tt_aufstellung_render.dump")
)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

. (Join-Path $PSScriptRoot "load-env.ps1")

if (-not $RenderUrl) { $RenderUrl = $env:RENDER_DATABASE_URL }
if (-not $RenderUrl) {
    throw "RENDER_DATABASE_URL is not set in .env"
}

function Find-PgTool($name) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($ver in @("18", "17")) {
        $candidate = "C:\Program Files\PostgreSQL\$ver\bin\$name.exe"
        if (Test-Path $candidate) { return $candidate }
    }
    throw "PostgreSQL tool '$name' not found. Install PostgreSQL 18+ or Docker."
}

$pgDump = Find-PgTool "pg_dump"
$pgRestore = Find-PgTool "pg_restore"
$psql = Find-PgTool "psql"

$docker = Get-Command docker -ErrorAction SilentlyContinue
$useDocker = $false
if ($docker) {
    try {
        & docker info --format "{{.ServerVersion}}" 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { $useDocker = $true }
    } catch {}
}

Write-Host "Dumping Render database (read-only) ..." -ForegroundColor Cyan
& $pgDump $RenderUrl --no-owner --no-acl --format=custom -f $DumpFile
if (-not (Test-Path $DumpFile)) { throw "Dump file was not created." }
$dumpSize = (Get-Item $DumpFile).Length
Write-Host "Dump size: $dumpSize bytes"
if ($dumpSize -lt 1024) { throw "Dump failed or is empty. Check pg_dump version and Render connectivity." }

if ($useDocker) {
    $container = "tt-aufstellung-postgres"
    $running = docker ps --filter "name=$container" --format "{{.Names}}" 2>$null
    if (-not $running) {
        docker compose up -d postgres
        Start-Sleep -Seconds 8
    }
    Write-Host "Resetting local Docker schema ..." -ForegroundColor Yellow
    docker exec $container psql -U tt -d tt_aufstellung -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
    docker cp $DumpFile "${container}:/tmp/tt_aufstellung_render.dump"
    docker exec $container pg_restore -U tt -d tt_aufstellung --no-owner --no-acl /tmp/tt_aufstellung_render.dump
    docker exec $container rm -f /tmp/tt_aufstellung_render.dump
    $verifyPsql = "docker exec $container psql -U tt -d tt_aufstellung"
} else {
    Write-Host "Using native PostgreSQL on localhost (docker-compose credentials)." -ForegroundColor Yellow
    $localUrl = $env:DATABASE_URL
    if (-not $localUrl) { $localUrl = "postgresql+psycopg://tt:tt_dev@localhost:5432/tt_aufstellung" }
    # Convert SQLAlchemy URL to psql URI
    $localPg = $localUrl -replace "^postgresql\+psycopg2?://", "postgresql://"

    $superPass = $env:POSTGRES_SUPERUSER_PASSWORD
    $adminUri = if ($superPass) {
        "postgresql://postgres:$superPass@127.0.0.1:5432/postgres"
    } else {
        "postgresql://postgres@127.0.0.1:5432/postgres"
    }

    Write-Host "Ensuring local role/database exist (postgres superuser) ..."
    & $psql $adminUri -v ON_ERROR_STOP=1 -c @"
DO `$`$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'tt') THEN CREATE ROLE tt LOGIN PASSWORD 'tt_dev'; END IF;
END `$`$;
SELECT 'CREATE DATABASE tt_aufstellung OWNER tt' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'tt_aufstellung')\gexec
"@

    Write-Host "Resetting local schema ..." -ForegroundColor Yellow
    & $psql $localPg -v ON_ERROR_STOP=1 -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
    & $pgRestore --dbname=$localPg --no-owner --no-acl $DumpFile
    $verifyPsql = "& $psql $localPg"
}

Write-Host "Verifying row counts ..." -ForegroundColor Cyan
if ($useDocker) {
    docker exec $container psql -U tt -d tt_aufstellung -c @"
SELECT 'xttv_matches' AS table, COUNT(*)::text AS count FROM xttv_matches
UNION ALL SELECT 'xttv_players', COUNT(*)::text FROM xttv_players
UNION ALL SELECT 'match_players', COUNT(*)::text FROM match_players;
"@
} else {
    & $psql $localPg -c @"
SELECT 'xttv_matches' AS table, COUNT(*)::text AS count FROM xttv_matches
UNION ALL SELECT 'xttv_players', COUNT(*)::text FROM xttv_players
UNION ALL SELECT 'match_players', COUNT(*)::text FROM match_players;
"@
}

Write-Host "Done. Start the app with .\scripts\start-dev.ps1" -ForegroundColor Green
