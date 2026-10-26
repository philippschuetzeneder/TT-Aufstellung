"""Compare top Luftenberg lineups at several recency half-lives."""
import app.analysis_service as mod
from app.analysis_service import _raw_team_lineup_scenarios, _sharpen_scenarios
from app.db import SessionLocal

TEAM = "Luftenberg 1"

def top(hl):
    mod.LINEUP_RECENCY_HALF_LIFE_MATCHES = hl
    mod._match_rounds_ago_cache.clear()
    with SessionLocal() as db:
        scenarios, names = _raw_team_lineup_scenarios(db, TEAM)
    sharp = _sharpen_scenarios(scenarios)
    print(f"\nHALF {hl}")
    for prob, order in sharp[:4]:
        print(f"  {prob*100:5.1f}%  {' / '.join(names.get(p, p) for p in order)}")

for hl in (8, 6, 5, 4, 3.5, 3):
    top(hl)
