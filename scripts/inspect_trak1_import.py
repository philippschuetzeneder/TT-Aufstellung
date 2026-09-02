"""Inspect missing match_players for Tragwein/Kamig 1 incomplete matches."""
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from sqlalchemy import text
from app.db import SessionLocal

TEAM = "Tragwein/Kamig 1"
db = SessionLocal()
rows = db.execute(
    text(
        """
    SELECT m.id, m.match_date, m.home_team, m.away_team, m.external_id,
           mp.side, mp.name, mp.position, mp.external_player_id
    FROM xttv_matches m
    LEFT JOIN match_players mp ON mp.match_id = m.id
    WHERE m.season = '2025/2026'
      AND (m.home_team = :team OR m.away_team = :team)
    ORDER BY to_date(substring(m.match_date from 1 for 10), 'DD.MM.YYYY'), m.id, mp.side, mp.position
    """
    ),
    {"team": TEAM},
).mappings().all()

by_match = defaultdict(list)
meta = {}
for r in rows:
    by_match[r["id"]].append(r)
    meta[r["id"]] = r

print(f"=== {TEAM} match player detail ===\n")
for mid, players in sorted(by_match.items(), key=lambda x: meta[x[0]]["match_date"] or ""):
    m = meta[mid]
    side = "home" if m["home_team"] == TEAM else "away"
    all_side = [p for p in players if p["side"] == side]
    real_side = [
        p for p in all_side
        if p.get("external_player_id") and not str(p["external_player_id"]).startswith("__nopass_")
    ]
    opp = m["away_team"] if side == "home" else m["home_team"]
    print(
        f"{m['match_date']} vs {opp}  meid={m.get('external_id')}  "
        f"rows={len(all_side)} real_ids={len(real_side)}/4"
    )
    for p in sorted(all_side, key=lambda x: x.get("position") or ""):
        eid = p.get("external_player_id") or "NULL"
        flag = "" if eid != "NULL" and not str(eid).startswith("__nopass_") else " [NO REAL ID]"
        print(f"  {p.get('position') or '?'}  {p.get('name')}  id={eid}{flag}")
    if len(real_side) < 4:
        print("  >>> INCOMPLETE for backtest (needs 4 real external_player_id)")
    print()

db.close()
