# Push local PostgreSQL to production VPS and deploy latest main.
# WARNING: Replaces production DB contents. Requires SSH key and local pg_dump.
param(
    [string]$SshHost = "root@159.195.240.88",
    [string]$SshKey = "$env:USERPROFILE\.ssh\ttaufstellung",
    [string]$RemoteRoot = "/opt/tt-aufstellung",
    [string]$DumpFile = (Join-Path (Split-Path $PSScriptRoot -Parent) "tt_aufstellung_local_push.dump"),
    [switch]$SkipGitPull,
    [switch]$SkipDbPush
)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root
. (Join-Path $PSScriptRoot "load-env.ps1")

if (-not (Test-Path $SshKey)) { throw "SSH key not found: $SshKey" }

function Find-PgTool($name) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($ver in @("18", "17", "16")) {
        $candidate = "C:\Program Files\PostgreSQL\$ver\bin\$name.exe"
        if (Test-Path $candidate) { return $candidate }
    }
    throw "PostgreSQL tool '$name' not found."
}

$sshArgs = @("-i", $SshKey, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new", $SshHost)
$remoteDump = "/root/tt_aufstellung_local_push.dump"

if (-not $SkipDbPush) {
    $localUrl = $env:DATABASE_URL -replace "^postgresql\+psycopg2?://", "postgresql://"
    if (-not $localUrl) { throw "DATABASE_URL not set" }
    $pgContainer = "tt-aufstellung-postgres"
    $running = docker ps --filter "name=$pgContainer" --format "{{.Names}}" 2>$null
    if ($running) {
        Write-Host "Dumping local DB via Docker ($pgContainer, PG16-compatible) ..." -ForegroundColor Cyan
        docker exec $pgContainer pg_dump -U tt -d tt_aufstellung --no-owner --no-acl -Fc -f /tmp/local_push.dump
        if ($LASTEXITCODE -ne 0) { throw "docker pg_dump failed" }
        docker cp "${pgContainer}:/tmp/local_push.dump" $DumpFile
        docker exec $pgContainer rm -f /tmp/local_push.dump
    } else {
        $pgDump = Find-PgTool "pg_dump"
        Write-Host "Dumping local DB via pg_dump (plain SQL for cross-version restore) ..." -ForegroundColor Cyan
        $plainDump = [System.IO.Path]::ChangeExtension($DumpFile, ".sql")
        & $pgDump $localUrl --no-owner --no-acl --format=plain -f $plainDump
        if ($LASTEXITCODE -ne 0) { throw "pg_dump failed" }
        $DumpFile = $plainDump
        $script:UsePlainSqlRestore = $true
    }
    $size = (Get-Item $DumpFile).Length
    if ($size -lt 1024) { throw "Local dump too small ($size bytes)." }
    Write-Host "Local dump: $DumpFile ($size bytes)" -ForegroundColor Green

    Write-Host "Uploading dump to VPS ..." -ForegroundColor Cyan
    & scp -i $SshKey -o BatchMode=yes $DumpFile "${SshHost}:$remoteDump"
    if ($LASTEXITCODE -ne 0) { throw "scp failed" }
}

$gitPull = if ($SkipGitPull) { "echo skip git pull" } else { "git fetch origin && git checkout main && git pull --ff-only origin main" }

if ($SkipDbPush) {
    $dbBlock = ""
} else {
    $dbBlock = @"
echo '=== stopping app ==='
docker compose -f docker-compose.prod.yml stop app
echo '=== restoring database ==='
docker compose -f docker-compose.prod.yml exec -T postgres psql -U tt -d tt_aufstellung -c 'DROP SCHEMA public CASCADE; CREATE SCHEMA public;'
docker compose -f docker-compose.prod.yml cp '$remoteDump' postgres:/tmp/local_push.dump
docker compose -f docker-compose.prod.yml exec -T postgres pg_restore -U tt -d tt_aufstellung --no-owner --no-acl /tmp/local_push.dump || test `$? -le 1
docker compose -f docker-compose.prod.yml exec -T postgres rm -f /tmp/local_push.dump
rm -f '$remoteDump'
"@
}

$remoteScript = @"
set -euo pipefail
cd '$RemoteRoot'
$gitPull
echo '=== git HEAD ==='
git rev-parse --short HEAD
$dbBlock
echo '=== build & start ==='
docker compose -f docker-compose.prod.yml up -d --build
echo '=== RC audit ==='
docker compose -f docker-compose.prod.yml exec -T app python scripts/audit_rc_snapshots.py --global
echo '=== done ==='
"@

$remoteScript = ($remoteScript -replace "`r`n", "`n") -replace "`r", ""
Write-Host "Running remote deploy ..." -ForegroundColor Cyan
$remoteScript | & ssh @sshArgs "bash -s"
if ($LASTEXITCODE -ne 0) { throw "Remote deploy failed." }
Write-Host "Production deploy finished." -ForegroundColor Green
