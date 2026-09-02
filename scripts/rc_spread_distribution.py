"""RC spread distribution for all match quartets (uses production RC loader)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import text
from app.analysis_service import _load_latest_rc_map
from app.db import SessionLocal

db = SessionLocal()
all_sides = db.execute(
    text(
        """
    SELECT array_agg(mp.external_player_id::text ORDER BY mp.position) AS lineup
    FROM xttv_matches m
    JOIN match_players mp ON mp.match_id = m.id
    WHERE m.season = '2025/2026' AND mp.external_player_id IS NOT NULL
    GROUP BY m.id, CASE WHEN mp.side = 'home' THEN m.home_team ELSE m.away_team END
    HAVING count(*) = 4
    """
    )
).mappings().all()

all_ids = list({p for r in all_sides for p in r["lineup"]})
rc = _load_latest_rc_map(db, all_ids)

spreads = []
missing_rc = 0
for row in all_sides:
    vals = [rc[p] for p in row["lineup"] if p in rc]
    if len(vals) < 4:
        missing_rc += 1
        continue
    spreads.append(max(vals) - min(vals))

spreads.sort()
n = len(spreads)
print(f"4-player sides total: {len(all_sides)}")
print(f"With 4 RC values: {n}  (missing RC: {missing_rc})")
print(
    f"spread min={spreads[0]:.0f} p25={spreads[n//4]:.0f} "
    f"median={spreads[n//2]:.0f} p75={spreads[3*n//4]:.0f} max={spreads[-1]:.0f}"
)
for t in (40, 100, 150, 200):
    c = sum(1 for s in spreads if s >= t)
    print(f"  >={t}: {c}/{n} ({100*c/n:.1f}%)")
below40 = sum(1 for s in spreads if s < 40)
print(f"  <40: {below40}/{n} ({100*below40/n:.1f}%)")

# Tragwein/Kamig 1 roster vs match quartets
TEAM = "Tragwein/Kamig 1"
roster = db.execute(
    text(
        """
    SELECT DISTINCT mp.external_player_id::text AS pid
    FROM xttv_matches m JOIN match_players mp ON mp.match_id=m.id
    WHERE m.season='2025/2026' AND mp.external_player_id IS NOT NULL
      AND ((m.home_team=:t AND mp.side='home') OR (m.away_team=:t AND mp.side='away'))
    """
    ),
    {"t": TEAM},
).scalars().all()
rc_roster = _load_latest_rc_map(db, roster)
rv = sorted(rc_roster.values())
print(f"\n{TEAM} roster: {len(rv)} players with RC, spread={rv[-1]-rv[0]:.0f} ({rv[0]:.0f}-{rv[-1]:.0f})")

trak_rows = db.execute(
    text(
        """
    SELECT array_agg(mp.external_player_id::text ORDER BY mp.position) AS lineup
    FROM xttv_matches m JOIN match_players mp ON mp.match_id=m.id
    WHERE m.season='2025/2026' AND mp.external_player_id IS NOT NULL
      AND ((m.home_team=:t AND mp.side='home') OR (m.away_team=:t AND mp.side='away'))
    GROUP BY m.id HAVING count(*)=4
    """
    ),
    {"t": TEAM},
).mappings().all()
trak_spreads = []
for row in trak_rows:
    vals = [rc.get(p) for p in row["lineup"] if p in rc]
    if len(vals) >= 4:
        trak_spreads.append(max(vals) - min(vals))
if trak_spreads:
    trak_spreads.sort()
    tn = len(trak_spreads)
    print(f"{TEAM} per-match quartet spreads ({tn} matches with 4 players):")
    print(f"  min={trak_spreads[0]:.0f} median={trak_spreads[tn//2]:.0f} max={trak_spreads[-1]:.0f}")
    print(f"  >=40: {sum(1 for s in trak_spreads if s>=40)}/{tn}")

db.close()
