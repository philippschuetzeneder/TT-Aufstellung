"""One-off: Eder Alexander Passnr, singles per league, RC snapshots, trend."""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, "backend"))

from sqlalchemy import text

from app.analytics_service import league_player_stats, player_profile
from app.db import SessionLocal, create_all
from app.player_analysis_service import _league_group, resolve_latest_league_season

create_all()
db = SessionLocal()
resolved = resolve_latest_league_season(db, "421")
group = _league_group(resolved)
print("421 ->", resolved)
print("group ->", group)

rows = db.execute(
    text(
        """
        SELECT mp.external_player_id::text, max(mp.name), count(*) AS rows
        FROM match_players mp
        WHERE mp.name ILIKE '%Eder%Alexander%'
        GROUP BY mp.external_player_id
        ORDER BY rows DESC
        """
    ),
).fetchall()
print("\nEder Alexander IDs:", rows)

pid = rows[0][0] if rows else "25660"
player = db.execute(
    text(
        """
        SELECT external_player_id, name, club, rc_player_id
        FROM xttv_players WHERE external_player_id::text = :pid
        """
    ),
    {"pid": pid},
).mappings().first()
print("\nxttv_players:", dict(player) if player else None)

snaps = db.execute(
    text(
        """
        SELECT count(*) FROM player_rating_snapshots prs
        JOIN xttv_players xp ON xp.id = prs.player_id
        WHERE xp.external_player_id::text = :pid AND prs.source = 'ratingscentral'
        """
    ),
    {"pid": pid},
).scalar()
print("rc_snapshots:", snaps)

by_league = db.execute(
    text(
        """
        SELECT m.league, count(*) AS singles
        FROM match_players mp
        JOIN xttv_matches m ON m.id = mp.match_id
        JOIN match_games g ON g.match_id = m.id
          AND g.game_type = 'singles'
          AND (
            (mp.side = 'home' AND g.home_position = mp.position)
            OR (mp.side = 'away' AND g.away_position = mp.position)
          )
        WHERE mp.external_player_id::text = :pid
          AND g.result ~ '^\\s*[0-9]+\\s*:\\s*[0-9]+\\s*$'
        GROUP BY m.league
        ORDER BY singles DESC
        """
    ),
    {"pid": pid},
).fetchall()
in_group = sum(r.singles for r in by_league if group and str(r.league or "").startswith(group))
print(f"\nSingles total={sum(r.singles for r in by_league)} in_421_group={in_group}")
for r in by_league[:12]:
    mark = " *421*" if group and str(r.league or "").startswith(group) else ""
    print(f"  {r.singles:3}  {r.league}{mark}")

db.close()

t0 = time.perf_counter()
rank = league_player_stats("421")
print(f"\nleague_player_stats in {time.perf_counter() - t0:.2f}s, players={rank.get('count')}")
eder = next((p for p in rank.get("players", []) if str(p.get("id")) == str(pid)), None)
if not eder:
    eder = next((p for p in rank.get("players", []) if "Eder" in (p.get("name") or "") and "Alexander" in (p.get("name") or "")), None)
print("ranking row:", eder)

test_league = None
if by_league:
    test_league = by_league[0].league
if test_league:
    t1 = time.perf_counter()
    prof = player_profile(_league_group(test_league) or test_league, str(pid))
    print(f"\nplayer_profile({test_league!r}) in {time.perf_counter() - t1:.2f}s ok={prof.get('ok')}")
    if not prof.get("ok"):
        print("error:", prof.get("error"))
    else:
        for key in ("season", "cross_season"):
            sc = prof.get("scopes", {}).get(key, {})
            print(key, "matches=", sc.get("matches"), "rc_trend=", sc.get("rc_trend"))
        print("player rc_rating=", prof.get("player", {}).get("rc_rating"))
