# Read-only clone of production PostgreSQL (VPS Docker) into local Postgres.
# Does NOT modify production.
param(
    [string]$SshHost = "root@159.195.240.88",
    [string]$SshKey = "$env:USERPROFILE\.ssh\ttaufstellung",
    [string]$RemoteRoot = "/opt/tt-aufstellung",
    [string]$DumpFile = (Join-Path (Split-Path $PSScriptRoot -Parent) "tt_aufstellung_prod.dump")
)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

if (-not (Test-Path $SshKey)) {
    throw "SSH key not found: $SshKey"
}

function Find-PgTool($name) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($ver in @("18", "17", "16")) {
        $candidate = "C:\Program Files\PostgreSQL\$ver\bin\$name.exe"
        if (Test-Path $candidate) { return $candidate }
    }
    return $null
}

$pgRestore = Find-PgTool "pg_restore"
$psql = Find-PgTool "psql"

$docker = Get-Command docker -ErrorAction SilentlyContinue
if (-not $docker) { throw "Docker is required for local Postgres restore." }

$sshArgs = @("-i", $SshKey, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new", $SshHost)

Write-Host "Dumping production database (read-only on VPS) ..." -ForegroundColor Cyan
$remotePath = "/root/tt_aufstellung_prod.dump"
$dumpCmd = "docker compose -f $RemoteRoot/docker-compose.prod.yml exec -T postgres pg_dump -U tt -d tt_aufstellung --no-owner --no-acl -Fc > $remotePath && ls -la $remotePath"
& ssh @sshArgs $dumpCmd
if ($LASTEXITCODE -ne 0) { throw "Remote pg_dump failed." }

Write-Host "Downloading dump ..." -ForegroundColor Cyan
& scp -i $SshKey -o BatchMode=yes "${SshHost}:$remotePath" $DumpFile
if ($LASTEXITCODE -ne 0) { throw "scp failed." }
& ssh @sshArgs "rm -f $remotePath"

$dumpSize = (Get-Item $DumpFile).Length
Write-Host "Dump size: $dumpSize bytes"
if ($dumpSize -lt 1024) { throw "Dump file too small." }

$container = "tt-aufstellung-postgres"
$running = docker ps --filter "name=$container" --format "{{.Names}}" 2>$null
if (-not $running) {
    docker compose up -d postgres
    Start-Sleep -Seconds 8
}

Write-Host "Resetting local Docker schema ..." -ForegroundColor Yellow
docker exec $container psql -U tt -d tt_aufstellung -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
docker cp $DumpFile "${container}:/tmp/tt_aufstellung_prod.dump"
if ($pgRestore) {
    docker exec $container pg_restore -U tt -d tt_aufstellung --no-owner --no-acl /tmp/tt_aufstellung_prod.dump
} else {
    throw "pg_restore not found locally; install PostgreSQL client tools."
}
docker exec $container rm -f /tmp/tt_aufstellung_prod.dump

Write-Host "Verifying ..." -ForegroundColor Cyan
docker exec $container psql -U tt -d tt_aufstellung -c @"
SELECT season, COUNT(*) FROM xttv_matches GROUP BY season ORDER BY season;
SELECT COUNT(DISTINCT regexp_replace(league, ' 20[0-9]{2}/20[0-9]{2}$', '')) AS league_groups_2627
FROM xttv_matches WHERE season = '2026/2027';
"@

Write-Host "Done. Restart dev server: .\scripts\restart-dev.ps1" -ForegroundColor Green
