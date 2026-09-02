import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from sqlalchemy import text
from app.db import SessionLocal
from app.analysis_service import _load_latest_rc_map

db = SessionLocal()
for team in ["Friedburg 5", "Tragwein/Kamig 1"]:
    rows = db.execute(
        text(
            """
        SELECT array_agg(mp.external_player_id::text ORDER BY mp.position) AS lineup
        FROM xttv_matches m JOIN match_players mp ON mp.match_id=m.id
        WHERE m.season='2025/2026' AND mp.external_player_id IS NOT NULL
          AND ((m.home_team=:t AND mp.side='home') OR (m.away_team=:t AND mp.side='away'))
        GROUP BY m.id HAVING count(*)=4
        """
        ),
        {"t": team},
    ).mappings().all()
    ids = list({p for r in rows for p in r["lineup"]})
    rc = _load_latest_rc_map(db, ids)
    spreads = []
    for r in rows:
        v = [rc[p] for p in r["lineup"] if p in rc]
        if len(v) == 4:
            spreads.append(max(v) - min(v))
    print(f"{team}: {len(rows)} matches w/4, spreads={spreads}, min={min(spreads) if spreads else 'n/a'}")
db.close()
