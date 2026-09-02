import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import app.analysis_service as mod
from app.analysis_service import _load_latest_rc_map, _adaptive_strength_position_weights, _scenarios_from_strength_prior
from app.db import SessionLocal
from backtest_f1_rotating_teams import legacy_weights

players = ["24839", "25280", "40114", "78296"]
db = SessionLocal()
rc = _load_latest_rc_map(db, players)
db.close()
vals = [rc[p] for p in players]
print("RC:", {p: round(rc[p], 1) for p in players})
print("spread", max(vals)-min(vals))
print("weights current", _adaptive_strength_position_weights(players, rc))
print("weights legacy ", legacy_weights(players, rc))
cur = _scenarios_from_strength_prior(players, rc)[:5]
mod._adaptive_strength_position_weights = legacy_weights
leg = _scenarios_from_strength_prior(players, rc)[:5]
print("top5 current", [(round(p*100,2), o) for p,o in cur])
print("top5 legacy ", [(round(p*100,2), o) for p,o in leg])
