import time
from app.db import SessionLocal
from app.analysis_service import (
    _reference_date,
    _load_opponent_pool,
    _raw_team_lineup_scenarios,
    _load_player_profiles,
    _load_doubles_stats,
)

def tick(label, t0):
    now = time.monotonic()
    print(f"{label}: {now - t0:.3f}s")
    return now

t = time.monotonic()
with SessionLocal() as db:
    ref = _reference_date(db)
    t = tick("ref", t)
    pool = _load_opponent_pool(db, "Luftenberg 1", ref)
    t = tick(f"pool {len(pool)}", t)
    scenarios, names = _raw_team_lineup_scenarios(db, "Luftenberg 1", None, ref, pool)
    t = tick(f"scenarios {len(scenarios)}", t)
    ids = ["21772", "21773", "22183", "23754"]
    for _, order in scenarios[:6]:
        ids.extend(order)
    profiles = _load_player_profiles(db, list(set(ids)), ref)
    t = tick(f"profiles {len(profiles[0])}", t)
    _load_doubles_stats(db, ["21772", "21773", "22183", "23754"], list(pool)[:8], "Tragwein/Kamig 2", "Luftenberg 1", ref)
    tick("doubles", t)
