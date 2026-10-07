"""Import full RC PlayerHistory for mapped players in one league season roster."""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, "backend"))

from sqlalchemy import text

from app.analytics_service import _LEAGUE_STATS_CACHE
from app.db import SessionLocal, create_all
from app.models import XttvPlayer
from app.player_analysis_service import resolve_latest_league_season
from app.rc_import import import_rc_player

LEAGUE = sys.argv[1] if len(sys.argv) > 1 else "421 RK Linz Umg. / MV Ost"
MIN_SNAPSHOTS = 2
PAUSE_SEC = 2.5
MAX_ATTEMPTS = 4


def snapshot_count(session, player_db_id: int) -> int:
    return session.execute(
        text(
            """
            SELECT count(*) FROM player_rating_snapshots
            WHERE player_id = :pid AND source = 'ratingscentral'
            """
        ),
        {"pid": player_db_id},
    ).scalar() or 0


create_all()
with SessionLocal() as db:
    resolved = resolve_latest_league_season(db, LEAGUE)
    if not resolved:
        raise SystemExit(f"No league row for {LEAGUE!r}")
    ids = db.execute(
        text(
            """
            SELECT DISTINCT mp.external_player_id::text
            FROM match_players mp
            JOIN xttv_matches m ON m.id = mp.match_id
            WHERE m.league = :league AND mp.external_player_id IS NOT NULL
            """
        ),
        {"league": resolved},
    ).scalars().all()

print(f"Roster {resolved}: {len(ids)} players", flush=True)
ok = err = skip = already = 0
with SessionLocal() as db:
    by_pass = {
        str(p.external_player_id): p
        for p in db.query(XttvPlayer).filter(XttvPlayer.external_player_id.in_(list(ids))).all()
    }

for index, pid in enumerate(ids):
    player = by_pass.get(str(pid))
    if not player or player.rc_player_id is None:
        skip += 1
        continue
    with SessionLocal() as db:
        existing = snapshot_count(db, player.id)
    if existing >= MIN_SNAPSHOTS:
        already += 1
        continue
    if index > 0:
        time.sleep(PAUSE_SEC)
    last_exc = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            result = import_rc_player(
                int(player.rc_player_id),
                xttv_external_player_id=str(pid),
                xttv_name=player.name,
                xttv_club=player.club,
            )
            ok += 1
            print(
                f"OK {player.name} ({pid}): {result['snapshots_upserted']} snapshots",
                flush=True,
            )
            last_exc = None
            break
        except Exception as exc:
            last_exc = exc
            if attempt < MAX_ATTEMPTS:
                wait = PAUSE_SEC * attempt
                print(f"Retry {attempt}/{MAX_ATTEMPTS} {player.name}: {exc}", flush=True)
                time.sleep(wait)
    if last_exc is not None:
        err += 1
        print(f"ERR {player.name} ({pid}): {last_exc}", flush=True)

_LEAGUE_STATS_CACHE.clear()
print(f"Done: ok={ok} already_ok={already} skip={skip} err={err}", flush=True)
