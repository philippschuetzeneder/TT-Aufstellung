from __future__ import annotations
from itertools import permutations
import math
import time
from sqlalchemy import text
from .db import SessionLocal

WIN_TARGET = 8
SINGLE_GAMES = 12
MAX_ANALYSIS_SECONDS = 3.0


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


def _resolve_ids(db, ids):
    """Normalize IDs when possible, but never reject a UI-selected ID.

    The UI historically received player IDs from more than one source. A
    selected ID is therefore kept as-is if it cannot be mapped immediately;
    the targeted statistics loader can still resolve it by either XTTV
    external_player_id or match_players.id. Unknown IDs get the neutral prior
    rather than aborting the entire calculation.
    """
    requested = [str(x) for x in ids]
    if not requested:
        return [], {}, []
    rows = db.execute(text("""
        SELECT id::text AS db_id, external_player_id::text AS external_id, max(name) AS player_name
        FROM match_players
        WHERE external_player_id::text = ANY(:ids)
           OR id::text = ANY(:ids)
        GROUP BY id, external_player_id
    """), {"ids": requested}).mappings()
    by_requested = {}
    names = {}
    for r in rows:
        external = str(r["external_id"]) if r["external_id"] is not None else None
        db_id = str(r["db_id"])
        name = r["player_name"]
        if external:
            if external in requested:
                by_requested[external] = external
            if db_id in requested:
                by_requested[db_id] = external
            names[external] = name
    normalized = [by_requested.get(x, x) for x in requested]
    missing = [x for x in requested if x not in by_requested]
    return normalized, names, missing


def _load_targeted_raw_stats(db, ids, stats, names, matchups):
    ids = [str(x) for x in ids]
    rows = db.execute(text("""
        SELECT COALESCE(external_player_id::text, id::text) AS player_id, max(name) AS player_name
        FROM match_players
        WHERE external_player_id::text = ANY(:ids) OR id::text = ANY(:ids)
        GROUP BY COALESCE(external_player_id::text, id::text)
    """), {"ids": ids}).mappings()
    for r in rows:
        pid = str(r["player_id"])
        names[pid] = r["player_name"]
        stats.setdefault(pid, (0, 0))

    rows = db.execute(text("""
        SELECT COALESCE(p.external_player_id::text,p.id::text) AS player_id,
               count(g.id) AS games,
               count(g.id) FILTER (WHERE
                   (p.side='home' AND split_part(trim(g.result),':',1)::int > split_part(trim(g.result),':',2)::int)
                   OR (p.side='away' AND split_part(trim(g.result),':',2)::int > split_part(trim(g.result),':',1)::int)
               ) AS wins
        FROM match_players p
        JOIN match_games g ON g.match_id=p.match_id AND g.game_type='singles'
          AND ((p.side='home' AND p.position=g.home_position) OR (p.side='away' AND p.position=g.away_position))
        WHERE (p.external_player_id::text = ANY(:ids) OR p.id::text = ANY(:ids))
          AND g.result ~ '^\\s*[0-9]+\\s*:\\s*[0-9]+\\s*$'
        GROUP BY COALESCE(p.external_player_id::text,p.id::text)
    """), {"ids": ids}).mappings()
    for r in rows:
        stats[str(r["player_id"])] = (int(r["wins"] or 0), int(r["games"] or 0))

    rows = db.execute(text("""
        WITH games AS (
            SELECT COALESCE(hp.external_player_id::text,hp.id::text) AS home_id,
                   COALESCE(ap.external_player_id::text,ap.id::text) AS away_id,
                   CASE WHEN split_part(trim(g.result),':',1)::int > split_part(trim(g.result),':',2)::int THEN 1 ELSE 0 END AS home_win
            FROM match_games g
            JOIN match_players hp ON hp.match_id=g.match_id AND hp.side='home' AND hp.position=g.home_position
            JOIN match_players ap ON ap.match_id=g.match_id AND ap.side='away' AND ap.position=g.away_position
            WHERE g.game_type='singles' AND g.result ~ '^\\s*[0-9]+\\s*:\\s*[0-9]+\\s*$'
              AND (hp.external_player_id::text = ANY(:ids) OR hp.id::text = ANY(:ids))
              AND (ap.external_player_id::text = ANY(:ids) OR ap.id::text = ANY(:ids))
        )
        SELECT home_id AS player_id, away_id AS opponent_id, sum(home_win) AS wins, count(*) AS games
        FROM games GROUP BY home_id,away_id
        UNION ALL
        SELECT away_id, home_id, sum(1-home_win), count(*) FROM games GROUP BY away_id,home_id
    """), {"ids": ids}).mappings()
    for r in rows:
        matchups[(str(r["player_id"]), str(r["opponent_id"]))] = (int(r["wins"] or 0), int(r["games"] or 0))


def _load_cache(own, opponent_team, actual):
    db = SessionLocal()
    try:
        db.execute(text("SET statement_timeout = '1000ms'"))
        db.execute(text("SET lock_timeout = '250ms'"))

        own, own_names, _ = _resolve_ids(db, own)

        if actual is not None:
            actual, actual_names, _ = _resolve_ids(db, actual)
            lineup_key = ",".join(sorted(actual))
            rows = list(db.execute(text("SELECT p1,p2,p3,p4,appearances FROM analysis_lineup_orders WHERE lineup_key=:key ORDER BY appearances DESC LIMIT 24"), {"key": lineup_key}).mappings())
            total = sum(int(r["appearances"]) for r in rows)
            scenarios = [(int(r["appearances"]) / total, (str(r["p1"]), str(r["p2"]), str(r["p3"]), str(r["p4"]))) for r in rows] if total else [(1.0 / 24.0, tuple(o)) for o in permutations(actual)]
            relevant = set(own) | set(actual)
            source = "actual"
        else:
            rows = list(db.execute(text("SELECT p1,p2,p3,p4,appearances FROM analysis_lineup_orders WHERE team=:team ORDER BY appearances DESC LIMIT 24"), {"team": opponent_team}).mappings())
            total = sum(int(r["appearances"]) for r in rows)
            scenarios = [(int(r["appearances"]) / total, (str(r["p1"]), str(r["p2"]), str(r["p3"]), str(r["p4"]))) for r in rows] if total else []
            relevant = set(own)
            for _, order in scenarios:
                relevant.update(order)
            source = "predicted"
            actual_names = {}
            if not scenarios:
                return {}, {}, {}, [], source

        ids = list(relevant)
        rows = db.execute(text("SELECT player_id::text AS player_id,player_name,singles_wins,singles_games FROM analysis_player_stats WHERE player_id::text = ANY(:ids)"), {"ids": ids}).mappings()
        stats = {}
        names = dict(own_names)
        names.update(actual_names if actual is not None else {})
        for r in rows:
            pid = str(r["player_id"])
            names[pid] = r["player_name"]
            stats[pid] = (int(r["singles_wins"] or 0), int(r["singles_games"] or 0))

        rows = db.execute(text("SELECT player_id::text AS player_id,opponent_id::text AS opponent_id,wins,games FROM analysis_matchups WHERE player_id::text = ANY(:ids) AND opponent_id::text = ANY(:ids)"), {"ids": ids}).mappings()
        matchups = {(str(r["player_id"]), str(r["opponent_id"])): (int(r["wins"]), int(r["games"])) for r in rows}

        missing_stats = [pid for pid in ids if pid not in stats]
        if missing_stats:
            _load_targeted_raw_stats(db, missing_stats, stats, names, matchups)

        for pid in ids:
            names.setdefault(pid, pid)
            stats.setdefault(pid, (0, 0))

        return names, stats, matchups, scenarios, source
    except Exception as exc:
        raise RuntimeError(f"Analysis-Cache nicht verfügbar: {type(exc).__name__}: {exc}") from exc
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
            doubles = [_pair_probability(own_order[0], own_order[1], opp_order[0], opp_order[1], stats), _pair_probability(own_order[2], own_order[3], opp_order[2], opp_order[3], stats)]
            expected += scenario_probability * _team_win_probability(singles + doubles)
        evaluated.append({"own_player_ids": list(own_order), "players": [names.get(pid, pid) for pid in own_order], "team_win_probability": round(expected, 6)})
        if time.monotonic() - started > MAX_ANALYSIS_SECONDS:
            raise RuntimeError("Analysis exceeded the internal 3-second safety budget")

    evaluated.sort(key=lambda x: (-x["team_win_probability"], x["own_player_ids"]))
    for rank, item in enumerate(evaluated, 1):
        item["rank"] = rank
    elapsed = time.monotonic() - started
    return {"ok": True, "phase": "B" if actual else "A", "opponent_team": opponent_team, "own_player_ids": own, "opponent_set_source": source, "recommendation": evaluated[0], "recommendations": evaluated, "opponent_predictions": [{"player_ids": list(o), "players": [{"id": p, "name": names.get(p,p)} for p in o], "probability": p} for p,o in scenarios], "model": {"version": "strength-h2h-doubles-v15-tolerant-id-resolution", "win_target": WIN_TARGET, "single_games": SINGLE_GAMES, "doubles_games": 2, "opponent_lineups": "observed historical position orders", "actual_opponent_positions": "historical distribution; all 24 permutations if unseen"}, "data_quality": {"scenario_variants": len(scenarios), "own_orders_evaluated": 24, "runtime_seconds": round(elapsed,4), "runtime_data_source": "targeted-analysis-cache-with-tolerant-id-resolution"}}