"""
Top-k sweep for low-history quartets (0–2 exact-4 games before match).
Season 2025/2026, leakage-safe. Script-only.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import timedelta

from sqlalchemy import text

from backtest_opponent_lineups import (
    leakage_safe,
    predict_scenarios,
    extract_side_order,
    _lineup_cohesion_lb,
    _patched_reference_date,
)
from app.analysis_service import _parse_match_date
from app.db import SessionLocal

SEASON = "2025/2026"
MAX_K = 24
THRESHOLD = 0.80


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
                    "match_date": parsed,
                    "opponent_team": info[team_key],
                    "opponent_ids": list(order),
                    "actual_order": order,
                }
            )
    return cases


def rank_of_actual(scenarios, actual):
    ranked = sorted(scenarios, key=lambda x: (-x[0], x[1]))
    for i, (_, order) in enumerate(ranked, 1):
        if order == actual:
            return i
    return MAX_K + 1


def main():
    db = SessionLocal()
    try:
        cases = load_cases_2526(db)
    finally:
        db.close()

    print(f"Saison {SEASON} · {len(cases)} Fälle gesamt")
    print("Filter: 0–2 exakte 4er-Spiele vor dem Spiel")
    print()

    scenario_cache = {}
    cohesion_cache = {}
    ranks = []
    by_exact = {0: [], 1: [], 2: []}

    db = SessionLocal()
    try:
        for i, case in enumerate(cases, 1):
            ref_end = case["match_date"] - timedelta(days=1)
            key = (case["opponent_team"], tuple(sorted(case["opponent_ids"])), ref_end)
            if key not in scenario_cache:
                scenario_cache[key] = predict_scenarios(
                    db, case["opponent_team"], case["opponent_ids"], ref_end,
                )
            if key not in cohesion_cache:
                with leakage_safe(ref_end):
                    exact, _, _, _ = _lineup_cohesion_lb(db, case["opponent_ids"], ref_end)
                cohesion_cache[key] = exact
            exact = cohesion_cache[key]
            if exact > 2:
                continue

            actual = tuple(case["actual_order"])
            rank = rank_of_actual(scenario_cache[key], actual)
            ranks.append(rank)
            if exact in by_exact:
                by_exact[exact].append(rank)

            if i % 1000 == 0:
                print(f"  ... scanned {i}/{len(cases)}", flush=True)
    finally:
        db.close()

    n = len(ranks)
    if not n:
        print("Keine Fälle mit 0–2 Historie.")
        return 1

    print(f"\nFälle mit 0–2 exakten 4er-Spielen: {n}")
    for e in (0, 1, 2):
        print(f"  exakt {e} Spiele: {len(by_exact[e])} Fälle")

    hits = {k: sum(1 for r in ranks if r <= k) for k in range(1, MAX_K + 1)}
    pct = {k: hits[k] / n for k in range(1, MAX_K + 1)}

    first_80 = next((k for k in range(1, MAX_K + 1) if pct[k] >= THRESHOLD), None)

    print("\n| Top-k | Treffer | Quote % |")
    print("|:---:|:---:|:---:|")
    for k in range(1, MAX_K + 1):
        marker = "  ← ≥80 %" if k == first_80 else ""
        print(f"| {k} | {hits[k]} | {pct[k]*100:.1f}{marker} |")

    if first_80:
        print(f"\nAb Top-{first_80}: Trefferquote ≥ 80 % ({pct[first_80]*100:.1f} %)")
    else:
        print(f"\nKein Top-k ≤ {MAX_K} erreicht 80 % (max Top-{MAX_K}: {pct[MAX_K]*100:.1f} %)")

    # Sub-buckets 0, 1, 2 separately
    print("\n--- je nach exakter Historie ---")
    for e in (0, 1, 2):
        sub = by_exact[e]
        if not sub:
            continue
        sn = len(sub)
        sub_first = next(
            (k for k in range(1, MAX_K + 1) if sum(1 for r in sub if r <= k) / sn >= THRESHOLD),
            None,
        )
        sub_pct = {k: sum(1 for r in sub if r <= k) / sn for k in (1, 3, 5, 10, 15, 20, 24)}
        parts = ", ".join(f"Top-{k}: {sub_pct[k]*100:.1f}%" for k in (1, 3, 5, 10, 15, 20, 24))
        print(f"  {e} Spiele ({sn} Fälle): {parts}")
        if sub_first:
            print(f"    → ≥80 % ab Top-{sub_first}")
        else:
            print(f"    → Top-24: {sub_pct[24]*100:.1f}%")

    return 0


if __name__ == "__main__":
    sys.exit(main())
