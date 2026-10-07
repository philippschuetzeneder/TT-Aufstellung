import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, "backend"))
from sqlalchemy import text
from app.db import SessionLocal
from app.player_analysis_service import resolve_latest_league_season, current_season

db = SessionLocal()
print("current_season", current_season())
print("421 resolved", resolve_latest_league_season(db, "421"))
rows = db.execute(text(
    "SELECT league, count(*) AS c FROM xttv_matches WHERE league LIKE '421%' GROUP BY league ORDER BY league DESC LIMIT 8"
)).fetchall()
print("421 rows", rows)
rows2 = db.execute(text(
    "SELECT mp.external_player_id::text, max(mp.name), xp.rc_player_id FROM match_players mp "
    "LEFT JOIN xttv_players xp ON xp.external_player_id = mp.external_player_id::text "
    "WHERE mp.name ILIKE '%Meisinger%' GROUP BY 1,3 LIMIT 5"
)).fetchall()
print("Meisinger", rows2)
db.close()
