"""F1-only backtest on high-rotation teams vs stable teams."""
from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent / "backend"))

import app.analysis_service as mod
from app.analysis_service import clear_analysis_runtime_caches
from app.db import SessionLocal
from backtest_opponent_lineups import _lineup_cohesion_lb, leakage_safe
from backtest_opponent_lineups_2526 import load_cases_2526, rank_of_actual
from validate_canvas_fixes import predict_scenarios

TOP_K = [1, 3, 5, 10]

ROTATING_TEAMS = [
    "Friedburg 5",
    "U.Gutau/U.Pregarten 8",
    "Luftenberg 2",
    "Linz Waldegg 4",
    "Tragwein/Kamig 1",
    "Saxen 5",
]

STABLE_TEAMS = [
    "Lasberg 1",
    "Tragwein/Kamig 3",
]


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


def scenarios_for(db, case, ref_end, legacy_f1: bool):
    if not legacy_f1:
        return predict_scenarios(db, case["opponent_team"], case["opponent_ids"], ref_end)
    saved = mod._adaptive_strength_position_weights
    mod._adaptive_strength_position_weights = legacy_weights
    clear_analysis_runtime_caches()
    try:
        return predict_scenarios(db, case["opponent_team"], case["opponent_ids"], ref_end)
    finally:
        mod._adaptive_strength_position_weights = saved
        clear_analysis_runtime_caches()


def evaluate(cases: list[dict]) -> dict:
    cache: dict[tuple, dict] = {}
    totals = {"current": {k: 0 for k in TOP_K}, "legacy_f1": {k: 0 for k in TOP_K}}
    cohesion = {"exact0": 0, "exact_le2": 0, "n": 0}
    db = SessionLocal()
    try:
        for i, case in enumerate(cases, 1):
            ref_end = case["match_date"] - timedelta(days=1)
            actual = tuple(case["actual_order"])
            key = (case["opponent_team"], tuple(sorted(case["opponent_ids"])), ref_end)
            if key not in cache:
                cache[key] = {
                    "current": scenarios_for(db, case, ref_end, False),
                    "legacy_f1": scenarios_for(db, case, ref_end, True),
                }
            with leakage_safe(ref_end):
                exact, _, _, _ = _lineup_cohesion_lb(db, case["opponent_ids"], ref_end)
            cohesion["n"] += 1
            if exact == 0:
                cohesion["exact0"] += 1
            if exact <= 2:
                cohesion["exact_le2"] += 1
            for mode in ("current", "legacy_f1"):
                rank = rank_of_actual(cache[key][mode], actual)
                for k in TOP_K:
                    if rank <= k:
                        totals[mode][k] += 1
            if i % 20 == 0:
                print(f"    ... {i}/{len(cases)}", flush=True)
    finally:
        db.close()
    n = len(cases)
    return {
        "cases": n,
        "cohesion": cohesion,
        "current": {str(k): totals["current"][k] / n for k in TOP_K},
        "legacy_f1": {str(k): totals["legacy_f1"][k] / n for k in TOP_K},
        "delta_pp": {str(k): (totals["current"][k] - totals["legacy_f1"][k]) / n * 100 for k in TOP_K},
    }


def filter_team(cases, team: str) -> list[dict]:
    return [c for c in cases if c["opponent_team"] == team]


def filter_teams(cases, teams: list[str]) -> list[dict]:
    wanted = set(teams)
    return [c for c in cases if c["opponent_team"] in wanted]


def main() -> None:
    db = SessionLocal()
    try:
        all_cases = load_cases_2526(db)
    finally:
        db.close()

    report = {"f1_only_comparison": True, "teams": {}, "groups": {}}

    print("=== F1-only: current vs legacy strength weights ===\n")
    for team in ROTATING_TEAMS + STABLE_TEAMS:
        cases = filter_team(all_cases, team)
        if len(cases) < 4:
            print(f"{team}: only {len(cases)} cases, skip")
            continue
        print(f"{team} ({len(cases)} Fälle)...")
        result = evaluate(cases)
        report["teams"][team] = result
        print(
            f"  exact4=0: {result['cohesion']['exact0']}/{result['cohesion']['n']} | "
            f"Top-1 {result['current']['1']*100:.1f}% vs {result['legacy_f1']['1']*100:.1f}% "
            f"({result['delta_pp']['1']:+.1f} PP) | "
            f"Top-3 {result['delta_pp']['3']:+.1f} PP"
        )

    rot_cases = filter_teams(all_cases, ROTATING_TEAMS)
    stab_cases = filter_teams(all_cases, STABLE_TEAMS)
    print(f"\n=== Pooled ROTATING ({len(rot_cases)} Fälle) ===")
    report["groups"]["rotating_pooled"] = evaluate(rot_cases)
    r = report["groups"]["rotating_pooled"]
    print(
        f"  Top-1 {r['current']['1']*100:.1f}% vs {r['legacy_f1']['1']*100:.1f}% ({r['delta_pp']['1']:+.1f} PP)"
    )
    print(f"  Top-3 {r['current']['3']*100:.1f}% vs {r['legacy_f1']['3']*100:.1f}% ({r['delta_pp']['3']:+.1f} PP)")

    print(f"\n=== Pooled STABLE ({len(stab_cases)} Fälle) ===")
    report["groups"]["stable_pooled"] = evaluate(stab_cases)
    s = report["groups"]["stable_pooled"]
    print(
        f"  Top-1 {s['current']['1']*100:.1f}% vs {s['legacy_f1']['1']*100:.1f}% ({s['delta_pp']['1']:+.1f} PP)"
    )
    print(f"  Top-3 {s['current']['3']*100:.1f}% vs {s['legacy_f1']['3']*100:.1f}% ({s['delta_pp']['3']:+.1f} PP)")

    out = SCRIPT_DIR / "output" / "f1_rotating_teams_backtest.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWritten {out}")


if __name__ == "__main__":
    main()
