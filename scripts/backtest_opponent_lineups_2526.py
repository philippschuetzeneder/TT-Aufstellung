"""
Backtest opponent lineup ranking — season 2025/2026, all leagues.

Assumes known 4-player quartets; evaluates full A/B/C/D permutation ranking.
Leakage-safe: only data strictly before each match day.
Script-only — does not modify app code.
"""
from __future__ import annotations

import contextlib
import json
import sys
from collections import defaultdict
from datetime import date, timedelta
from itertools import permutations
from pathlib import Path

from sqlalchemy import text

# Reuse leakage-safe helpers from sibling script
from backtest_opponent_lineups import (
    leakage_safe,
    predict_scenarios,
    extract_side_order,
    _lineup_cohesion_lb,
    _load_latest_rc_map_lb,
    _patched_reference_date,
)
from app.analysis_service import _scenarios_from_strength_prior
from app.db import SessionLocal

SEASON = "2025/2026"
TOP_K = [1, 3, 5, 10]


def load_cases_2526(db):
    rows = db.execute(
        text(
            """
        SELECT m.id AS match_id, m.match_date, m.home_team, m.away_team,
               mp.side, mp.external_player_id AS player_id, mp.position
        FROM xttv_matches m
        JOIN match_players mp ON mp.match_id = m.id
        WHERE mp.external_player_id IS NOT NULL
          AND m.season = :season
          AND m.match_date IS NOT NULL
        ORDER BY to_date(substring(m.match_date from 1 for 10), 'DD.MM.YYYY'), m.id
        """
        ),
        {"season": SEASON},
    ).mappings().all()

    by_match = defaultdict(lambda: defaultdict(list))
    meta = {}
    for row in rows:
        mid = row["match_id"]
        meta[mid] = {
            "match_date": row["match_date"],
            "home_team": row["home_team"],
            "away_team": row["away_team"],
        }
        by_match[mid][row["side"]].append(row)

    from app.analysis_service import _parse_match_date

    cases = []
    for mid, sides in by_match.items():
        info = meta[mid]
        parsed = _parse_match_date(info["match_date"])
        if not parsed:
            continue
        for side, team_key in [("home", "home_team"), ("away", "away_team")]:
            if len(sides.get(side, [])) != 4:
                continue
            order = extract_side_order(sides[side])
            if not order:
                continue
            cases.append(
                {
                    "match_id": mid,
                    "match_date": parsed,
                    "opponent_team": info[team_key],
                    "opponent_ids": list(order),
                    "actual_order": order,
                }
            )
    return cases


def rank_of_actual(scenarios, actual: tuple[str, ...]) -> int:
    ranked = sorted(scenarios, key=lambda x: (-x[0], x[1]))
    for i, (_, order) in enumerate(ranked, 1):
        if order == actual:
            return i
    return 25


def strength_scenarios(db, player_ids, ref_end: date):
    with leakage_safe(ref_end):
        rc_map = _load_latest_rc_map_lb(db, player_ids)
    return _scenarios_from_strength_prior(player_ids, rc_map)


def run(cases, progress_every=250):
    totals = {
        "model": {k: 0 for k in TOP_K},
        "strength": {k: 0 for k in TOP_K},
        "n": 0,
    }
    cohesion_detail = {
        "0": {k: 0 for k in TOP_K},
        "1-3": {k: 0 for k in TOP_K},
        "4+": {k: 0 for k in TOP_K},
    }
    cohesion_n = {"0": 0, "1-3": 0, "4+": 0}

    scenario_cache: dict = {}
    strength_cache: dict = {}
    cohesion_cache: dict = {}

    db = SessionLocal()
    try:
        for i, case in enumerate(cases, 1):
            ref_end = case["match_date"] - timedelta(days=1)
            actual = tuple(case["actual_order"])
            key = (case["opponent_team"], tuple(sorted(case["opponent_ids"])), ref_end)

            if key not in scenario_cache:
                scenario_cache[key] = predict_scenarios(
                    db, case["opponent_team"], case["opponent_ids"], ref_end,
                )
            model_rank = rank_of_actual(scenario_cache[key], actual)

            if key not in strength_cache:
                strength_cache[key] = strength_scenarios(db, case["opponent_ids"], ref_end)
            strength_rank = rank_of_actual(strength_cache[key], actual)

            if key not in cohesion_cache:
                with leakage_safe(ref_end):
                    exact, _, _, _ = _lineup_cohesion_lb(db, case["opponent_ids"], ref_end)
                cohesion_cache[key] = exact
            exact = cohesion_cache[key]
            if exact == 0:
                bucket = "0"
            elif exact <= 3:
                bucket = "1-3"
            else:
                bucket = "4+"

            totals["n"] += 1
            cohesion_n[bucket] += 1
            for k in TOP_K:
                if model_rank <= k:
                    totals["model"][k] += 1
                    cohesion_detail[bucket][k] += 1
                if strength_rank <= k:
                    totals["strength"][k] += 1

            if progress_every and i % progress_every == 0:
                print(f"  ... {i}/{len(cases)}", flush=True)
    finally:
        db.close()

    n = totals["n"]
    result = {
        "season": SEASON,
        "cases": n,
        "leakage": "data only before match day (ref_end = match_date - 1)",
        "model": {
            "label": "Produktionsmodell (Historie + Positions-/Stärke-Prior, ohne Sharpening)",
            "top_k_pct": {str(k): totals["model"][k] / n for k in TOP_K},
        },
        "strength": {
            "label": "Stärke-Ranking (RC: stärkester auf A, schwächster auf D)",
            "top_k_pct": {str(k): totals["strength"][k] / n for k in TOP_K},
        },
        "delta_model_minus_strength_pp": {
            str(k): (totals["model"][k] - totals["strength"][k]) / n * 100 for k in TOP_K
        },
        "cohesion_buckets": {},
    }
    for bucket in cohesion_detail:
        bn = cohesion_n[bucket]
        if bn:
            result["cohesion_buckets"][bucket] = {
                "n": bn,
                "model_top_k_pct": {str(k): cohesion_detail[bucket][k] / bn for k in TOP_K},
            }
    return result


def print_summary(result):
    n = result["cases"]
    print(f"\n=== Gegner-Aufstellung Backtest · Saison {result['season']} ===")
    print(f"Fälle (4er mit gültiger A/B/C/D): {n}")
    print(f"Leakage: {result['leakage']}")
    print()
    print("| Top-k | Modell % | Stärke % | Δ Modell − Stärke |")
    print("|:---:|:---:|:---:|:---:|")
    for k in TOP_K:
        m = result["model"]["top_k_pct"][str(k)] * 100
        s = result["strength"]["top_k_pct"][str(k)] * 100
        d = result["delta_model_minus_strength_pp"][str(k)]
        print(f"| Top-{k} | {m:.1f} | {s:.1f} | {d:+.1f} PP |")
    print()
    print("Cohesion (exakte 4er-Spiele vor dem Spiel) — Modell Top-k:")
    for bucket, data in result.get("cohesion_buckets", {}).items():
        parts = [f"Top-{k}: {data['model_top_k_pct'][str(k)]*100:.1f}%" for k in TOP_K]
        print(f"  {bucket} Spiele ({data['n']} Fälle): {', '.join(parts)}")


def main():
    out = Path(__file__).resolve().parent / "output" / "opponent_lineup_backtest_2526.json"
    print(f"Loading cases for season {SEASON}...")
    db = SessionLocal()
    try:
        cases = load_cases_2526(db)
    finally:
        db.close()
    print(f"Cases: {len(cases)}")
    if not cases:
        return 1

    result = run(cases)
    print_summary(result)

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"\nJSON: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
