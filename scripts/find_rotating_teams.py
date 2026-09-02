"""Find teams with highest player rotation via SQL."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from sqlalchemy import text
from app.db import SessionLocal

db = SessionLocal()
rows = db.execute(
    text(
        """
WITH side_quartets AS (
  SELECT CASE WHEN mp.side='home' THEN m.home_team ELSE m.away_team END AS team,
         m.id AS match_id,
         array_agg(mp.external_player_id::text ORDER BY mp.position) AS lineup
  FROM xttv_matches m
  JOIN match_players mp ON mp.match_id = m.id
  WHERE m.season = '2025/2026' AND mp.external_player_id IS NOT NULL
  GROUP BY team, m.id
  HAVING count(*) = 4
),
player_sets AS (
  SELECT team, match_id,
         (SELECT array_agg(x ORDER BY x) FROM unnest(lineup) AS x) AS player_set
  FROM side_quartets
)
SELECT team,
       count(*) AS matches,
       count(DISTINCT player_set) AS unique_quartets,
       round(count(DISTINCT player_set)::numeric / count(*), 2) AS rotation_ratio
FROM player_sets
GROUP BY team
HAVING count(*) >= 8
ORDER BY rotation_ratio DESC, matches DESC
LIMIT 25
"""
    )
).mappings().all()
for r in rows:
    print(
        f"{float(r['rotation_ratio']):>4.2f}  {r['matches']:3} matches  "
        f"{r['unique_quartets']:3} quartets  {r['team']}"
    )
db.close()
