from __future__ import annotations

from itertools import permutations
import math
import time
from sqlalchemy import text
from .db import SessionLocal
from .analysis_cache import ensure_analysis_cache

WIN_TARGET = 8
SINGLE_GAMES = 12
SCHEDULE = ((0,0),(1,1),(2,2),(3,3),(0,1),(1,0),(2,3),(3,2),(0,2),(2,0),(1,3),(3,1))


def _strength(wins, games):
    return (wins + 5.0) / (games + 10.0)


def _matchup_probability(a, b, stats, matchups):
    aw, ag = stats.get(a, (0, 0)); bw, bg = stats.get(b, (0, 0))
    base = 1.0 / (1.0 + math.exp(-7.0 * (_strength(aw, ag) - _strength(bw, bg))))
    wins, games = matchups.get((a, b), (0, 0))
    if not games:
        return base
    direct = (wins + 2.0) / (games + 4.0)
    weight = min(0.55, games / 12.0)
    return (1.0 - weight) * base + weight * direct


def _pair_probability(a, b, c, d, stats):
    left = (_strength(*stats.get(a, (0, 0))) + _strength(*stats.get(b, (0, 0)))) / 2.0
    right = (_strength(*stats.get(c, (0, 0))) + _strength(*stats.get(d, (0, 0)))) / 2.0
    return 1.0 / (1.0 + math.exp(-7.0 * (left - right)))


def _team_win_probability(probs):
    distribution = [1.0]
    for p in probs:
        nxt = [0.0] * (len(distribution) + 1)
        for wins, mass in enumerate(distribution):
            nxt[wins] += mass * (1.0 - p)
            nxt[wins + 1] += mass * p
        distribution = nxt
    return sum(distribution[WIN_TARGET:])


def _load_cache(own, opponent_team, actual):
    # IMPORTANT: a warm cache is read-only here. It must never be rebuilt while
    # an HTTP analysis request is running. The first installation is the only
    # case where ensure_analysis_cache() can do synchronous work.
    ensure_analysis_cache()
    db = SessionLocal()
    try:
        if actual is not None:
            lineup_key = ",".join(sorted(actual))
            rows = list(db.execute(text("""
                SELECT p1,p2,p3,p4,appearances FROM analysis_lineup_orders
                WHERE lineup_key=:key ORDER BY appearances DESC
            """), {"key": lineup_key}).mappings())
            scenarios = []
            total = sum(int(r["appearances"]) for r in rows)
            if total:
                scenarios = [(int(r["appearances"]) / total, (str(r["p1"]), str(r["p2"]), str(r["p3"]), str(r["p4"]))) for r in rows]
            if not scenarios:
                scenarios = [(1.0 / 24.0, tuple(o)) for o in permutations(actual)]
            source = "actual"
        else:
            rows = list(db.execute(text("""
                SELECT p1,p2,p3,p4,appearances FROM analysis_lineup_orders
                WHERE team=:team ORDER BY appearances DESC LIMIT 24
            """), {"team": opponent_team}).mappings())
            total = sum(int(r["appearances"]) for r in rows)
            scenarios = [(int(r["appearances"]) / total, (str(r["p1"]), str(r["p2"]), str(r["p3"]), str(r["p4"]))) for r in rows] if total else []
            source = "predicted"
            if not scenarios:
                return {}, {}, {}, [], source

        relevant = set(own)
        for _, order in scenarios:
            relevant.update(order)
        ids = list(relevant)
        if not ids:
            return {}, {}, {}, scenarios, source

        # Only load statistics for the 4 own players + players occurring in the
        # opponent scenarios. Never scan the complete cache tables.
        params = {f"p{i}": pid for i, pid in enumerate(ids)}
        placeholders = ",".join(f":p{i}" for i in range(len(ids)))
        names = {}
        stats = {}
        for r in db.execute(text(f"""
            SELECT player_id,player_name,singles_wins,singles_games
            FROM analysis_player_stats WHERE player_id IN ({placeholders})
        """), params).mappings():
            pid = str(r["player_id"]); names[pid] = r["player_name"]
            stats[pid] = (int(r["singles_wins"]), int(r["singles_games"]))

        # Only the relevant cross-matchups can influence the 24x24 analysis.
        # This keeps the request independent of the size of the historical DB.
        matchups = {}
        for r in db.execute(text(f"""
            SELECT player_id,opponent_id,wins,games
            FROM analysis_matchups
            WHERE player_id IN ({placeholders}) AND opponent_id IN ({placeholders})
        """), {**params, **{f"p{i+len(ids)}": pid for i, pid in enumerate(ids)}}).mappings():
            matchups[(str(r["player_id"]), str(r["opponent_id"]))] = (int(r["wins"]), int(r["games"]))
        return names, stats, matchups, scenarios, source
    finally:
        db.close()


def analyze_lineup(own_player_ids, opponent_team, actual_opponent_ids=None, opponent_limit=24):
    started = time.monotonic()
    own = [str(x) for x in own_player_ids]
    if len(own) != 4 or len(set(own)) != 4:
        raise ValueError("exactly four different own_player_ids are required")
    if not opponent_team:
        raise ValueError("opponent_team is required")
    actual = None if actual_opponent_ids is None else [str(x) for x in actual_opponent_ids]
    if actual is not None and (len(actual) != 4 or len(set(actual)) != 4):
        raise ValueError("exactly four different actual_opponent_ids are required")

    names, stats, matchups, scenarios, source = _load_cache(own, opponent_team, actual)
    missing = [p for p in own if p not in names]
    if missing:
        raise ValueError(f"unknown own player IDs: {missing}")
    if not scenarios:
        return {"ok": True, "phase": "B" if actual is not None else "A", "recommendations": [], "warnings": ["Keine historische Viereraufstellung für diesen Gegner gefunden."]}

    evaluated = []
    for own_order in permutations(own):
        expected = 0.0
        for scenario_probability, opp_order in scenarios:
            singles = [_matchup_probability(own_order[h], opp_order[a], stats, matchups) for h, a in SCHEDULE]
            doubles = [
                _pair_probability(own_order[0], own_order[1], opp_order[0], opp_order[1], stats),
                _pair_probability(own_order[2], own_order[3], opp_order[2], opp_order[3], stats),
            ]
            expected += scenario_probability * _team_win_probability(singles + doubles)
        evaluated.append({"own_player_ids": list(own_order), "players": [names.get(pid, pid) for pid in own_order], "team_win_probability": round(expected, 6)})

    evaluated.sort(key=lambda x: (-x["team_win_probability"], x["own_player_ids"]))
    for rank, item in enumerate(evaluated, 1): item["rank"] = rank
    elapsed = time.monotonic() - started
    return {
        "ok": True,
        "phase": "B" if actual is not None else "A",
        "opponent_team": opponent_team,
        "own_player_ids": own,
        "opponent_set_source": source,
        "recommendation": evaluated[0],
        "recommendations": evaluated,
        "opponent_predictions": [{"player_ids": list(o), "players": [{"id": p, "name": names.get(p, p)} for p in o], "probability": p} for p, o in scenarios],
        "model": {"version": "strength-h2h-doubles-v10-targeted-cache", "win_target": WIN_TARGET, "single_games": SINGLE_GAMES, "doubles_games": 2, "opponent_lineups": "observed historical position orders", "actual_opponent_positions": "historical distribution; all 24 permutations if unseen"},
        "data_quality": {"scenario_variants": len(scenarios), "own_orders_evaluated": 24, "runtime_seconds": round(elapsed, 4), "runtime_data_source": "targeted_analysis_cache"},
    }
