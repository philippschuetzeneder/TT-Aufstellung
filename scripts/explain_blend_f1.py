"""Show why 70% strength + 30% position can still yield identical top after F1 change."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import app.analysis_service as mod
from app.analysis_service import (
    _blend_scenario_maps,
    _load_latest_rc_map,
    _load_player_position_priors,
    _scenarios_from_position_priors,
    _scenarios_from_strength_prior,
)
from app.db import SessionLocal
from backtest_f1_rotating_teams import legacy_weights

players = ["24839", "25280", "40114", "78296"]
strength_w = 0.70
pos_w = 1.0 - strength_w

db = SessionLocal()
position_priors = _load_player_position_priors(db, players)
rc = _load_latest_rc_map(db, players)
db.close()

pos_scenarios = _scenarios_from_position_priors(players, position_priors)
pos_map = {o: p for p, o in pos_scenarios}

mod._adaptive_strength_position_weights = legacy_weights
str_leg = _scenarios_from_strength_prior(players, rc)
mod._adaptive_strength_position_weights = __import__(
    "app.analysis_service", fromlist=["_adaptive_strength_position_weights"]
)._adaptive_strength_position_weights
str_cur = _scenarios_from_strength_prior(players, rc)

for label, str_scenarios in [("CURRENT F1", str_cur), ("LEGACY F1", str_leg)]:
    str_map = {o: p for p, o in str_scenarios}
    blended = _blend_scenario_maps(pos_map, str_map, pos_w)
    top3 = sorted(blended.items(), key=lambda x: -x[1])[:3]
    print(f"\n=== {label} (blend {pos_w:.0%} position + {strength_w:.0%} strength) ===")
    for order, prob in top3:
        p_pos = pos_map.get(order, 0)
        p_str = str_map.get(order, 0)
        print(f"  {prob*100:.2f}%  pos={p_pos*100:.2f}% str={p_str*100:.2f}%  {order}")

# Show where they differ
str_map_c = {o: p for p, o in str_cur}
str_map_l = {o: p for p, o in str_leg}
diff_orders = [o for o in set(str_map_c) | set(str_map_l) if abs(str_map_c.get(o,0)-str_map_l.get(o,0))>1e-6]
print(f"\nStrength prior differs on {len(diff_orders)}/24 permutations")
if diff_orders:
    o = max(diff_orders, key=lambda x: abs(str_map_c.get(x,0)-str_map_l.get(x,0)))
    print(f"Max diff order {o}: cur={str_map_c.get(o,0)*100:.3f}% leg={str_map_l.get(o,0)*100:.3f}%")
