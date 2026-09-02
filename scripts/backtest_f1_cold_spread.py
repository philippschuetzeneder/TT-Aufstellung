"""F1 backtest on ALL cases: exact4=0 and RC spread>=40 (F1 can actually differ)."""
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
from backtest_opponent_lineups_2526 import load_cases_2526
from backtest_f1_rotating_teams import evaluate, legacy_weights

from backtest_f1_rotating_spread import strength_prior_differs, rc_spread


def main() -> None:
    db = SessionLocal()
    try:
        all_cases = load_cases_2526(db)
    finally:
        db.close()

    cold_big_spread = []
    f1_changes = []
    db = SessionLocal()
    try:
        for case in all_cases:
            ref_end = case["match_date"] - timedelta(days=1)
            with leakage_safe(ref_end):
                exact, _, _, _ = _lineup_cohesion_lb(db, case["opponent_ids"], ref_end)
            spread, rc = rc_spread(db, case["opponent_ids"], ref_end)
            differs, _, _ = strength_prior_differs(case["opponent_ids"], rc)
            if exact == 0 and spread >= 40:
                cold_big_spread.append(case)
            if differs:
                f1_changes.append({**case, "spread": spread, "exact4": exact})
    finally:
        db.close()

    print(f"Total cases: {len(all_cases)}")
    print(f"exact4=0 AND rc_spread>=40: {len(cold_big_spread)}")
    print(f"F1 changes strength-prior top: {len(f1_changes)}")

    for name, cases in [
        ("cold_big_spread", cold_big_spread),
        ("f1_changes_all", [{k: v for k, v in c.items() if k in (
            "match_id", "match_date", "opponent_team", "opponent_ids", "actual_order",
        )} for c in f1_changes]),
    ]:
        if len(cases) < 5:
            print(f"\n{name}: {len(cases)} (too few)")
            continue
        print(f"\n=== {name} ({len(cases)} Fälle) ===")
        r = evaluate(cases)
        print(
            f"Top-1 {r['current']['1']*100:.1f}% vs {r['legacy_f1']['1']*100:.1f}% "
            f"({r['delta_pp']['1']:+.1f} PP)"
        )
        print(
            f"Top-3 {r['current']['3']*100:.1f}% vs {r['legacy_f1']['3']*100:.1f}% "
            f"({r['delta_pp']['3']:+.1f} PP)"
        )

    if f1_changes:
        print("\nF1-change cases by team:")
        from collections import Counter
        for team, n in Counter(c["opponent_team"] for c in f1_changes).most_common(10):
            print(f"  {team}: {n}")

    out = SCRIPT_DIR / "output" / "f1_cold_spread_backtest.json"
    out.write_text(json.dumps({"cold_big_spread_n": len(cold_big_spread), "f1_changes_n": len(f1_changes)}, indent=2))
    print(f"\nWritten {out}")


if __name__ == "__main__":
    main()
