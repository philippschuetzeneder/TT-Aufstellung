"""F1 backtest: rotating teams WITH large RC spread (where F1 actually differs)."""
from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent / "backend"))

import app.analysis_service as mod
from app.analysis_service import _resolve_rc_rating, clear_analysis_runtime_caches
from app.db import SessionLocal
from backtest_opponent_lineups import _lineup_cohesion_lb, leakage_safe
from backtest_opponent_lineups_2526 import load_cases_2526, rank_of_actual
from backtest_f1_rotating_teams import evaluate, legacy_weights, scenarios_for

TOP_K = [1, 3, 5, 10]
ROTATING_TEAMS = {
    "Friedburg 5", "U.Gutau/U.Pregarten 8", "Luftenberg 2", "Linz Waldegg 4",
    "Tragwein/Kamig 1", "Saxen 5", "Attergau 2", "Ried im Traunkreis 7",
    "Waizenkirchen 2", "Kremsmünster 6",
}


def rc_spread(db, player_ids, ref_end):
    with leakage_safe(ref_end):
        rc = mod._load_latest_rc_map(db, player_ids)
    values = [_resolve_rc_rating(pid, rc, player_ids) for pid in player_ids]
    return max(values) - min(values), rc


def strength_prior_differs(player_ids, rc):
    saved = mod._adaptive_strength_position_weights
    from app.analysis_service import _scenarios_from_strength_prior

    cur_top = _scenarios_from_strength_prior(player_ids, rc)[0][1]
    mod._adaptive_strength_position_weights = legacy_weights
    clear_analysis_runtime_caches()
    leg_top = _scenarios_from_strength_prior(player_ids, rc)[0][1]
    mod._adaptive_strength_position_weights = saved
    clear_analysis_runtime_caches()
    return cur_top != leg_top, cur_top, leg_top


def main() -> None:
    db = SessionLocal()
    try:
        all_cases = load_cases_2526(db)
    finally:
        db.close()

    rotating = []
    f1_diff = []
    db = SessionLocal()
    try:
        for case in all_cases:
            if case["opponent_team"] not in ROTATING_TEAMS:
                continue
            ref_end = case["match_date"] - timedelta(days=1)
            with leakage_safe(ref_end):
                exact, _, _, _ = _lineup_cohesion_lb(db, case["opponent_ids"], ref_end)
            spread, rc = rc_spread(db, case["opponent_ids"], ref_end)
            differs, cur_top, leg_top = strength_prior_differs(case["opponent_ids"], rc)
            row = {**case, "exact4": exact, "rc_spread": spread, "f1_diff": differs}
            rotating.append(row)
            if differs:
                f1_diff.append(row)
    finally:
        db.close()

    print(f"Rotating team cases: {len(rotating)}")
    print(f"Cases where F1 changes strength-prior top order: {len(f1_diff)}")
    print(f"  with exact4=0: {sum(1 for r in f1_diff if r['exact4']==0)}")
    print(f"  with rc_spread>=40: {sum(1 for r in f1_diff if r['rc_spread']>=40)}")

    # Subsets
    subsets = {
        "all_rotating": rotating,
        "f1_differs": f1_diff,
        "f1_differs_exact0": [r for r in f1_diff if r["exact4"] == 0],
        "f1_differs_spread40": [r for r in f1_diff if r["rc_spread"] >= 40],
        "f1_differs_exact0_spread40": [
            r for r in f1_diff if r["exact4"] == 0 and r["rc_spread"] >= 40
        ],
    }

    report = {}
    print("\n=== F1-only backtest by subset ===")
    for name, cases in subsets.items():
        if len(cases) < 3:
            print(f"{name}: {len(cases)} cases (skip)")
            continue
        # strip extra keys for evaluate
        eval_cases = [{k: v for k, v in c.items() if k in (
            "match_id", "match_date", "opponent_team", "opponent_ids", "actual_order",
        )} for c in cases]
        print(f"\n{name} ({len(cases)} Fälle)...")
        result = evaluate(eval_cases)
        report[name] = result
        print(
            f"  Top-1 {result['current']['1']*100:.1f}% vs {result['legacy_f1']['1']*100:.1f}% "
            f"({result['delta_pp']['1']:+.1f} PP) | Top-3 {result['delta_pp']['3']:+.1f} PP"
        )

    # Show example where F1 changes top order
    if f1_diff:
        ex = f1_diff[0]
        ref_end = ex["match_date"] - timedelta(days=1)
        _, rc = rc_spread(SessionLocal(), ex["opponent_ids"], ref_end)
        d, cur_top, leg_top = strength_prior_differs(ex["opponent_ids"], rc)
        print(f"\nExample F1 change ({ex['opponent_team']}, spread={ex['rc_spread']:.0f}):")
        print(f"  current top: {cur_top}")
        print(f"  legacy top:  {leg_top}")

    out = SCRIPT_DIR / "output" / "f1_rotating_spread_backtest.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWritten {out}")


if __name__ == "__main__":
    main()
