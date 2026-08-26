"""Compare recency half-life 45 vs 60 on opponent lineup Top-k (sample)."""
import random
import sys
from datetime import timedelta

from backtest_opponent_lineups import (
    predict_scenarios,
    extract_side_order,
    leakage_safe,
    _lineup_cohesion_lb,
)
from collections import defaultdict
from sqlalchemy import text
from app.analysis_service import _parse_match_date, LINEUP_RECENCY_HALF_LIFE_DAYS
import app.analysis_service as svc
from app.db import SessionLocal

SEASON = "2025/2026"
SAMPLE = 1200
SEED = 42
TOP_K = [1, 3, 5, 10]
HALF_LIVES = [45.0, 60.0]


def load_cases(db):
    rows = db.execute(text("""
        SELECT m.id AS match_id, m.match_date, m.home_team, m.away_team,
               mp.side, mp.external_player_id AS player_id, mp.position
        FROM xttv_matches m JOIN match_players mp ON mp.match_id = m.id
        WHERE mp.external_player_id IS NOT NULL AND m.season = :s AND m.match_date IS NOT NULL
    """), {"s": SEASON}).mappings().all()
    by_match = defaultdict(lambda: defaultdict(list))
    meta = {}
    for row in rows:
        mid = row["match_id"]
        meta[mid] = {"match_date": row["match_date"], "home_team": row["home_team"], "away_team": row["away_team"]}
        by_match[mid][row["side"]].append(row)
    cases = []
    for mid, sides in by_match.items():
        info = meta[mid]
        parsed = _parse_match_date(info["match_date"])
        if not parsed:
            continue
        for side, tk in [("home", "home_team"), ("away", "away_team")]:
            if len(sides.get(side, [])) != 4:
                continue
            order = extract_side_order(sides[side])
            if order:
                cases.append({"match_date": parsed, "opponent_team": info[tk], "opponent_ids": list(order), "actual_order": order})
    return cases


def rank(scenarios, actual):
    ranked = sorted(scenarios, key=lambda x: (-x[0], x[1]))
    for i, (_, o) in enumerate(ranked, 1):
        if o == actual:
            return i
    return 25


def eval_half_life(cases, hl):
    svc.LINEUP_RECENCY_HALF_LIFE_DAYS = hl
    svc._lineup_cohesion_cache.clear()
    cache = {}
    hits = {k: 0 for k in TOP_K}
    hits0 = {k: 0 for k in TOP_K}
    n0 = 0
    cohesion_cache = {}
    db = SessionLocal()
    try:
        for case in cases:
            ref = case["match_date"] - timedelta(days=1)
            key = (case["opponent_team"], tuple(sorted(case["opponent_ids"])), ref, hl)
            if key not in cache:
                cache[key] = predict_scenarios(db, case["opponent_team"], case["opponent_ids"], ref)
            r = rank(cache[key], tuple(case["actual_order"]))
            for k in TOP_K:
                if r <= k:
                    hits[k] += 1
            ck = (case["opponent_team"], tuple(sorted(case["opponent_ids"])), ref)
            if ck not in cohesion_cache:
                with leakage_safe(ref):
                    cohesion_cache[ck] = _lineup_cohesion_lb(db, case["opponent_ids"], ref)[0]
            if cohesion_cache[ck] == 0:
                n0 += 1
                for k in TOP_K:
                    if r <= k:
                        hits0[k] += 1
    finally:
        db.close()
    n = len(cases)
    return {k: hits[k] / n for k in TOP_K}, {k: hits0[k] / n0 if n0 else 0 for k in TOP_K}, n, n0


def main():
    db = SessionLocal()
    cases = load_cases(db)
    db.close()
    if len(cases) > SAMPLE:
        cases = random.Random(SEED).sample(cases, SAMPLE)
    print(f"Sample {len(cases)} · Saison {SEASON}")
    print(f"Baseline half-life (prod): {LINEUP_RECENCY_HALF_LIFE_DAYS} Tage\n")
    for hl in HALF_LIVES:
        pct, pct0, n, n0 = eval_half_life(cases, hl)
        row = " | ".join(f"Top-{k}: {pct[k]*100:.1f}%" for k in TOP_K)
        row0 = " | ".join(f"Top-{k}: {pct0[k]*100:.1f}%" for k in TOP_K)
        print(f"Half-life {hl:.0f}d — gesamt: {row}")
        print(f"           0 Historie ({n0}): {row0}\n")
    return 0

if __name__ == "__main__":
    sys.exit(main())
