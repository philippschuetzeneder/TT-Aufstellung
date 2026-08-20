# Quick local database + API smoke check (requires venv and running Postgres).
$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

. (Join-Path $PSScriptRoot "load-env.ps1")

$venvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    throw "Run .\scripts\setup-local.ps1 first."
}

$env:PYTHONPATH = "backend"

Write-Host "== Database health ==" -ForegroundColor Cyan
& $venvPython -c @"
from app.db import database_health, SessionLocal, create_all
from app.models import XttvMatch, XttvPlayer, MatchPlayer, PlayerRatingSnapshot, RcPlayerIndex

health = database_health()
print('health:', health)
if not health.get('ok'):
    raise SystemExit('Database not reachable')

create_all()
with SessionLocal() as s:
    print('xttv_matches:', s.query(XttvMatch).count())
    print('xttv_players:', s.query(XttvPlayer).count())
    print('match_players:', s.query(MatchPlayer).count())
    print('player_rating_snapshots:', s.query(PlayerRatingSnapshot).count())
    print('rc_player_index:', s.query(RcPlayerIndex).count())
"@
