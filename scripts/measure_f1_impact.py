"""Measure F1 (strength prior weights) impact in isolation."""
from __future__ import annotations

import contextlib
import sys
from datetime import timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent / "backend"))

import app.analysis_service as mod
from app.analysis_service import _adaptive_strength_weight, clear_analysis_runtime_caches
from app.db import SessionLocal
from backtest_opponent_lineups import _lineup_cohesion_lb, leakage_safe
from backtest_opponent_lineups_2526 import load_cases_2526, rank_of_actual
from validate_canvas_fixes import legacy_pre_canvas, predict_scenarios


def legacy_weights(player_ids, rc_by_player):
    rc_values = sorted(mod._resolve_rc_rating(pid, rc_by_player, player_ids) for pid in player_ids)
    spread = rc_values[-1] - rc_values[0]
    if spread < mod.STRENGTH_RC_SPREAD_FLAT_THRESHOLD:
        return mod.STRENGTH_POSITION_WEIGHTS_TIGHT
    gaps = [rc_values[i + 1] - rc_values[i] for i in range(3)]
    total_gap = sum(gaps) or 1.0
    normalized = [gap / total_gap for gap in gaps]
    w_min = mod.STRENGTH_POSITION_WEIGHT_MIN
    w_span = mod.STRENGTH_POSITION_WEIGHT_MAX - mod.STRENGTH_POSITION_WEIGHT_MIN
    w_a = w_min + w_span * (0.25 + 0.75 * normalized[2])
    w_d = w_min + w_span * (0.25 + 0.75 * normalized[0])
    w_b = w_min + w_span * (0.25 + 0.75 * (normalized[1] + normalized[2]) / 2.0)
    w_c = w_min + w_span * (0.25 + 0.75 * (normalized[0] + normalized[1]) / 2.0)
    return (w_a, w_b, w_c, w_d)


def top1_pct(cases, mode: str) -> float:
    cache: dict = {}
    hits = 0
    db = SessionLocal()
    try:
        for case in cases:
            ref_end = case["match_date"] - timedelta(days=1)
            actual = tuple(case["actual_order"])
            key = (case["opponent_team"], tuple(sorted(case["opponent_ids"])), ref_end)
            if key not in cache:
                if mode == "current":
                    cache[key] = predict_scenarios(db, case["opponent_team"], case["opponent_ids"], ref_end)
                elif mode == "legacy":
                    with legacy_pre_canvas():
                        cache[key] = predict_scenarios(db, case["opponent_team"], case["opponent_ids"], ref_end)
                elif mode == "f1only":
                    saved = mod._adaptive_strength_position_weights
                    mod._adaptive_strength_position_weights = legacy_weights
                    clear_analysis_runtime_caches()
                    cache[key] = predict_scenarios(db, case["opponent_team"], case["opponent_ids"], ref_end)
                    mod._adaptive_strength_position_weights = saved
                    clear_analysis_runtime_caches()
            if rank_of_actual(cache[key], actual) <= 1:
                hits += 1
    finally:
        db.close()
    return hits / len(cases) * 100 if cases else 0.0


def main() -> None:
    db = SessionLocal()
    try:
        cases = load_cases_2526(db)
        high_sw = []
        for case in cases:
            ref_end = case["match_date"] - timedelta(days=1)
            with leakage_safe(ref_end):
                sw, exact, _ = _adaptive_strength_weight(db, case["opponent_ids"], ref_end)
            if sw >= 0.70:
                high_sw.append((sw, exact, case))
        high_sw.sort(key=lambda x: -x[0])
        sample = [c for _, _, c in high_sw[:150]]
    finally:
        db.close()

    print(f"High strength-weight cases (sw>=0.70): {len(high_sw)}, sample={len(sample)}")
    for mode in ("current", "f1only", "legacy"):
        print(f"  Top-1 {mode}: {top1_pct(sample, mode):.1f}%")


if __name__ == "__main__":
    main()
