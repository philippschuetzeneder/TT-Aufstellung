# One-time local setup: venv, Python deps, optional Postgres via Docker.
$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

function Require-Command($name, $installHint) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        throw "Missing command '$name'. $installHint"
    }
}

Write-Host "== TT-Aufstellung local setup ==" -ForegroundColor Cyan

Require-Command python "Install Python 3.12: winget install Python.Python.3.12"

$pyVersion = python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($pyVersion -notmatch "^3\.(11|12)") {
    Write-Warning "Python $pyVersion detected. Render uses 3.12; 3.11+ should work."
}

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example"
}

if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment ..."
    python -m venv .venv
}

$venvPython = Join-Path $Root ".venv\Scripts\python.exe"
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r backend\requirements.txt

Write-Host ""
Write-Host "Python dependencies installed." -ForegroundColor Green

if (Get-Command docker -ErrorAction SilentlyContinue) {
    Write-Host "Starting PostgreSQL via Docker Compose ..."
    docker compose up -d postgres
    Write-Host "Waiting for Postgres healthcheck ..."
    $deadline = (Get-Date).AddSeconds(60)
    while ((Get-Date) -lt $deadline) {
        $status = docker inspect --format='{{.State.Health.Status}}' tt-aufstellung-postgres 2>$null
        if ($status -eq "healthy") { break }
        Start-Sleep -Seconds 2
    }
    if ($status -ne "healthy") {
        Write-Warning "Postgres container not healthy yet. Check: docker compose logs postgres"
    } else {
        Write-Host "PostgreSQL is ready (docker)." -ForegroundColor Green
    }
} else {
    Write-Warning "Docker not found. Install Docker Desktop or a local PostgreSQL 16+ instance."
    Write-Warning "Expected connection: postgresql+psycopg://tt:tt_dev@localhost:5432/tt_aufstellung"
}

Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Clone Render data:  .\scripts\clone-render-db.ps1"
Write-Host "  2. Start dev server:   .\scripts\start-dev.ps1"
Write-Host "  3. Open GUI:           http://localhost:10000/"
