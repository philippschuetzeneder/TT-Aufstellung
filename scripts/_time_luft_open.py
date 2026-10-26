import time

from sqlalchemy import text

from app.analysis_service import analyze_lineup
from app.db import SessionLocal

with SessionLocal() as db:
    rows = db.execute(
        text(
            """
            SELECT external_player_id::text, max(name)
            FROM match_players
            WHERE name ILIKE '%Schützeneder Philipp%'
               OR name ILIKE '%Hahn Julian%'
               OR name ILIKE '%Undesser Manuel%'
               OR name ILIKE '%Schmalzer Martin%'
            GROUP BY external_player_id
            """
        )
    ).all()
print("players", rows)
ids = [r[0] for r in rows]
started = time.monotonic()
result = analyze_lineup(ids[:4], "Luftenberg 1", None, own_is_home=True, own_team="Tragwein/Kamig 2")
elapsed = time.monotonic() - started
opp = (result.get("most_likely_opponent") or {}).get("players") or []
print("elapsed", round(elapsed, 3), "ok", result.get("ok"), "budget", result.get("analysis_elapsed_seconds"))
print("top", [(p.get("name"),) for p in opp], "p", (result.get("most_likely_opponent") or {}).get("probability"))
print("adv", (result.get("recommendation") or {}).get("advantage_vs_strength_lineup_pp"))
