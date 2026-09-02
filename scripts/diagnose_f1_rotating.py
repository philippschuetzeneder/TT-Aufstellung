"""Diagnose why F1 changes don't move backtest ranks on rotating teams."""
from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent / "backend"))

import app.analysis_service as mod
from app.analysis_service import (
    _build_combined_prior_scenarios,
    _build_known_four_opponent_scenarios,
    _scenarios_from_strength_prior,
    clear_analysis_runtime_caches,
)
from app.db import SessionLocal
from backtest_opponent_lineups import leakage_safe
from backtest_opponent_lineups_2526 import load_cases_2526, rank_of_actual
from backtest_f1_rotating_teams import legacy_weights, scenarios_for

TEAM = "Friedburg 5"


def main() -> None:
    db = SessionLocal()
    try:
        cases = [c for c in load_cases_2526(db) if c["opponent_team"] == TEAM]
    finally:
        db.close()

    case = cases[0]
    ref_end = case["match_date"] - timedelta(days=1)
    actual = tuple(case["actual_order"])
    print(f"Team: {TEAM}, case 1, actual rank target")

    db = SessionLocal()
    try:
        with leakage_safe(ref_end):
            cur_full, _, src = _build_known_four_opponent_scenarios(
                db, case["opponent_team"], case["opponent_ids"], ref_end,
            )
            prior_cur, prior_src = _build_combined_prior_scenarios(
                db, case["opponent_ids"], ref_end,
            )
            rc = mod._load_latest_rc_map(db, case["opponent_ids"])
            strength_cur = _scenarios_from_strength_prior(case["opponent_ids"], rc)

            mod._adaptive_strength_position_weights = legacy_weights
            clear_analysis_runtime_caches()
            leg_full, _, _ = _build_known_four_opponent_scenarios(
                db, case["opponent_team"], case["opponent_ids"], ref_end,
            )
            prior_leg, _ = _build_combined_prior_scenarios(db, case["opponent_ids"], ref_end)
            strength_leg = _scenarios_from_strength_prior(case["opponent_ids"], rc)
            mod._adaptive_strength_position_weights = mod._adaptive_strength_position_weights
            # restore - need original
    finally:
        db.close()

    from app.analysis_service import _adaptive_strength_position_weights as cur_w
    mod._adaptive_strength_position_weights = legacy_weights
    strength_leg = _scenarios_from_strength_prior(case["opponent_ids"], rc)
    mod._adaptive_strength_position_weights = cur_w

    print("source:", src)
    print("prior source:", prior_src)
    print("\nStrength prior top-3 CURRENT:")
    for p, o in strength_cur[:3]:
        print(f"  {p*100:.2f}% {o}")
    print("Strength prior top-3 LEGACY F1:")
    for p, o in strength_leg[:3]:
        print(f"  {p*100:.2f}% {o}")
    print("\nCombined prior top-3 CURRENT:")
    for p, o in prior_cur[:3]:
        print(f"  {p*100:.2f}% {o}")
    print("Combined prior top-3 LEGACY F1:")
    for p, o in prior_leg[:3]:
        print(f"  {p*100:.2f}% {o}")
    print("\nFull scenarios top-3 CURRENT:")
    for p, o in cur_full[:3]:
        print(f"  {p*100:.2f}% {o}")
    print("Full scenarios top-3 LEGACY F1:")
    for p, o in leg_full[:3]:
        print(f"  {p*100:.2f}% {o}")

    db = SessionLocal()
    try:
        cur = scenarios_for(db, case, ref_end, False)
        leg = scenarios_for(db, case, ref_end, True)
    finally:
        db.close()
    print(f"\nActual rank CURRENT: {rank_of_actual(cur, actual)}")
    print(f"Actual rank LEGACY:  {rank_of_actual(leg, actual)}")
    print(f"Scenarios identical: {cur == leg}")

    diff = 0
    cur_map = {o: p for p, o in cur}
    leg_map = {o: p for p, o in leg}
    for o in set(cur_map) | set(leg_map):
        if abs(cur_map.get(o, 0) - leg_map.get(o, 0)) > 1e-9:
            diff += 1
    print(f"Permutation prob diffs: {diff}/24")


if __name__ == "__main__":
    main()
