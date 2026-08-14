from __future__ import annotations
from itertools import permutations
import math
import time
from sqlalchemy import text, bindparam
from .db import SessionLocal

WIN_TARGET = 8
SINGLE_GAMES = 12
MAX_ANALYSIS_SECONDS = 5.0


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


def _load_analysis_data(own, opponent_team, actual):
    """Load only compact analysis-cache data; player IDs remain opaque UI IDs."""
    db = SessionLocal()
    try:
        db.execute(text("SET statement_timeout = '5000ms'"))
        db.execute(text("SET lock_timeout = '500ms'"))
        own = [str(x) for x in own]
        actual = None if actual is None else [str(x) for x in actual]

        if actual is not None:
            lineup_key = ",".join(sorted(actual))
            rows = list(db.execute(text("""
                SELECT p1,p2,p3,p4,appearances
                FROM analysis_lineup_orders
                WHERE lineup_key=:key
                ORDER BY appearances DESC
                LIMIT 24
            """), {"key": lineup_key}).mappings())
            total = sum(int(r["appearances"] or 0) for r in rows)
            if total:
                scenarios = [(int(r["appearances"]) / total, tuple(str(r[k]) for k in ("p1","p2","p3","p4"))) for r in rows]
            else:
                scenarios = [(1.0 / 24.0, tuple(order)) for order in permutations(actual)]
            source = "actual"
        else:
            rows = list(db.execute(text("""
                SELECT p1,p2,p3,p4,appearances
                FROM analysis_lineup_orders
                WHERE team=:team
                ORDER BY appearances DESC
                LIMIT 24
            """), {"team": opponent_team}).mappings())
            total = sum(int(r["appearances"] or 0) for r in rows)
            scenarios = [(int(r["appearances"]) / total, tuple(str(r[k]) for k in ("p1","p2","p3","p4"))) for r in rows] if total else []
            source = "predicted"

        if not scenarios:
            return {}, {}, {}, [], source

        relevant = set(own)
        for _, order in scenarios:
            relevant.update(order)
        ids = list(relevant)

        stats = {}
        names = {}
        stats_stmt = text("""
            SELECT player_id::text AS player_id, player_name, singles_wins, singles_games
            FROM analysis_player_stats
            WHERE player_id::text IN :ids
        """).bindparams(bindparam("ids", expanding=True))
        for r in db.execute(stats_stmt, {"ids": ids}).mappings():
            pid = str(r["player_id"])
            names[pid] = r["player_name"]
            stats[pid] = (int(r["singles_wins"] or 0), int(r["singles_games"] or 0))

        matchup_stmt = text("""
            SELECT player_id::text AS player_id, opponent_id::text AS opponent_id, wins, games
            FROM analysis_matchups
            WHERE player_id::text IN :ids
              AND opponent_id::text IN :ids
        """).bindparams(bindparam("ids", expanding=True))
        matchups = {}
        for r in db.execute(matchup_stmt, {"ids": ids}).mappings():
            matchups[(str(r["player_id"]), str(r["opponent_id"]))] = (int(r["wins"] or 0), int(r["games"] or 0))

        for pid in ids:
            stats.setdefault(pid, (0, 0))
            names.setdefault(pid, pid)
        return names, stats, matchups, scenarios, source
    except Exception as exc:
        raise RuntimeError(f"Analyse-Daten konnten nicht geladen werden: {type(exc).__name__}: {exc}") from exc
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

    names, stats, matchups, scenarios, source = _load_analysis_data(own, opponent_team, actual)
    if not scenarios:
        return {"ok": True, "phase": "B" if actual is not None else "A", "recommendations": [], "warnings": ["Keine historische Viereraufstellung für diesen Gegner gefunden."]}

    schedule = ((0,0),(1,1),(2,2),(3,3),(0,1),(1,0),(2,3),(3,2),(0,2),(2,0),(1,3),(3,1))
    relevant = set(own)
    for _, order in scenarios:
        relevant.update(order)
    matchup_p = {(a,b): _matchup_probability(a,b,stats,matchups) for a in relevant for b in relevant if a != b}

    evaluated = []
    for own_order in permutations(own):
        expected = 0.0
        for scenario_probability, opp_order in scenarios:
            singles = [matchup_p.get((own_order[h], opp_order[a]), 0.5) for h,a in schedule]
            doubles = [
                _pair_probability(own_order[0], own_order[1], opp_order[0], opp_order[1], stats),
                _pair_probability(own_order[2], own_order[3], opp_order[2], opp_order[3], stats),
            ]
            expected += scenario_probability * _team_win_probability(singles + doubles)
        evaluated.append({"own_player_ids": list(own_order), "players": [names.get(pid, pid) for pid in own_order], "team_win_probability": round(expected, 6)})
        if time.monotonic() - started > MAX_ANALYSIS_SECONDS:
            raise RuntimeError("Analysis exceeded the internal 5-second safety budget")

    evaluated.sort(key=lambda x: (-x["team_win_probability"], x["own_player_ids"]))
    for rank, item in enumerate(evaluated, 1):
        item["rank"] = rank
    elapsed = time.monotonic() - started
    return {
        "ok": True,
        "phase": "B" if actual else "A",
        "opponent_team": opponent_team,
        "own_player_ids": own,
        "opponent_set_source": source,
        "recommendation": evaluated[0],
        "recommendations": evaluated,
        "opponent_predictions": [{"player_ids": list(o), "players": [{"id": p, "name": names.get(p,p)} for p in o], "probability": p} for p,o in scenarios],
        "model": {"version": "strength-h2h-doubles-v17", "win_target": WIN_TARGET, "single_games": SINGLE_GAMES, "doubles_games": 2, "opponent_lineups": "observed historical position orders", "actual_opponent_positions": "historical distribution; all 24 permutations if unseen"},
        "data_quality": {"scenario_variants": len(scenarios), "own_orders_evaluated": 24, "runtime_seconds": round(elapsed,4), "runtime_data_source": "analysis-cache-only", "missing_player_stats_use_neutral_prior": True},
    }
