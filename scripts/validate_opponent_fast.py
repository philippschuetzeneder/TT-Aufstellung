"""Fast validation: opponent scenarios on unique keys only."""
from __future__ import annotations

import json
import random
import sys
import time
from datetime import timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent / "backend"))

from validate_canvas_fixes import (  # noqa: E402
    TOP_K,
    delta_pp,
    legacy_pre_canvas,
    opponent_backtest_dual,
    predict_scenarios,
)
from app.db import SessionLocal
from backtest_opponent_lineups import leakage_safe, _lineup_cohesion_lb
from backtest_opponent_lineups_2526 import load_cases_2526, rank_of_actual

UNIQUE_KEYS = 350
SEED = 42


def main():
    db = SessionLocal()
    try:
        cases = load_cases_2526(db)
    finally:
        db.close()

    rng = random.Random(SEED)
    by_key: dict = {}
    for case in cases:
        ref_end = case["match_date"] - timedelta(days=1)
        key = (case["opponent_team"], tuple(sorted(case["opponent_ids"])), ref_end)
        by_key.setdefault(key, []).append((tuple(case["actual_order"]), case))

    keys = list(by_key)
    rng.shuffle(keys)
    keys = keys[:UNIQUE_KEYS]

    eval_cases = []
    for key in keys:
        actual, case = rng.choice(by_key[key])
        eval_cases.append({**case, "actual_order": list(actual)})

    started = time.time()
    dual = opponent_backtest_dual(eval_cases)
    elapsed = time.time() - started

    out = {
        "unique_keys": len(keys),
        "cases": len(eval_cases),
        "runtime_sec": round(elapsed, 1),
        "current": dual["current"]["top_k_pct"],
        "legacy": dual["legacy"]["top_k_pct"],
        "delta_pp": {
            str(k): delta_pp(dual["current"]["top_k_pct"][str(k)], dual["legacy"]["top_k_pct"][str(k)])
            for k in TOP_K
        },
        "cohesion_current": dual["current"]["cohesion_buckets"],
        "cohesion_legacy": dual["legacy"]["cohesion_buckets"],
    }
    path = SCRIPT_DIR / "output" / "validate_opponent_fast.json"
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
