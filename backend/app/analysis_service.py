from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime
from itertools import permutations
import math
import re
import time

from sqlalchemy import text
from .db import SessionLocal

MAX_SINGLE_GAMES = 12
DOUBLES_GAMES = 2
WIN_TARGET = 8
CALCULATION_BUDGET_SECONDS = 10.0
RECENCY_HALF_LIFE_DAYS = 45.0


def _key(player_id, name):
    return str(player_id or f"name:{name}")


def _position_index(position):
    if not position:
        return None
    value = str(position).strip().upper()
    if value in "ABCD":
        return ord(value) - 65
    if value in {"1", "2", "3", "4"}:
        return int(value) - 1
    return None


def _score(result):
    m = re.fullmatch(r"\s*(\d+)\s*:\s*(\d+)\s*", result or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def _parse_date(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    value = str(value)
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M"):
        try:
            return datetime.strptime(value[:16], fmt).date()
        except ValueError:
            pass
    return None


def _recency_weight(match_date, reference_date):
    parsed = _parse_date(match_date)
    if not parsed or not reference_date:
        return 1.0
    return math.pow(0.5, max(0, (reference_date - parsed).days) / RECENCY_HALF_LIFE_DAYS)


def _load_snapshot(db):
    """Load the three normalized tables separately; never create a huge player x game Cartesian join."""
    matches = {}
    for row in db.execute(text("SELECT id, match_date, home_team, away_team FROM xttv_matches" )).mappings():
        matches[int(row["id"])] = {
            "id": int(row["id"]), "match_date": row["match_date"],
            "home_team": row["home_team"], "away_team": row["away_team"],
            "players": [], "games": []
        }
    for row in db.execute(text("SELECT match_id, name, external_player_id, side, position FROM match_players" )).mappings():
        match = matches.get(int(row["match_id"]))
        if match:
            match["players"].append({
                "id": _key(row["external_player_id"], row["name"]),
                "name": row["name"], "external_player_id": row["external_player_id"],
                "side": row["side"], "position": row["position"]
            })
    for row in db.execute(text("SELECT match_id, sequence, game_type, home_position, away_position, result FROM match_games" )).mappings():
        match = matches.get(int(row["match_id"]))
        if match:
            match["games"].append({
                "sequence": row["sequence"], "game_type": row["game_type"],
                "home_position": row["home_position"], "away_position": row["away_position"],
                "result": row["result"]
            })
    return list(matches.values())


def _build_stats(matches):
    overall = defaultdict(lambda: [0, 0])
    h2h = defaultdict(lambda: [0, 0])
    names = {}
    for match in matches:
        by_pos = {(p["side"], p["position"]): p for p in match["players"] if p["position"]}
        for p in match["players"]:
            names[p["id"]] = p["name"]
        for game in match["games"]:
            if game["game_type"] != "singles":
                continue
            score = _score(game["result"])
            if not score or score[0] == score[1]:
                continue
            home = by_pos.get(("home", game["home_position"]))
            away = by_pos.get(("away", game["away_position"]))
            if not home or not away:
                continue
            a, b = home["id"], away["id"]
            overall[a][1] += 1; overall[b][1] += 1
            h2h[(a, b)][1] += 1; h2h[(b, a)][1] += 1
            if score[0] > score[1]:
                overall[a][0] += 1; h2h[(a, b)][0] += 1
            else:
                overall[b][0] += 1; h2h[(b, a)][0] += 1
    return names, overall, h2h


def _strength(pid, overall):
    wins, games = overall.get(pid, (0, 0))
    return (wins + 5.0) / (games + 10.0)


def _matchup_probability(a, b, overall, h2h):
    base = 1.0 / (1.0 + math.exp(-7.0 * (_strength(a, overall) - _strength(b, overall))))
    wins, games = h2h.get((a, b), (0, 0))
    if not games:
        return base
    direct = (wins + 2.0) / (games + 4.0)
    weight = min(0.55, games / 12.0)
    return (1.0 - weight) * base + weight * direct


def _pair_probability(a, b, c, d, overall):
    left = (_strength(a, overall) + _strength(b, overall)) / 2.0
    right = (_strength(c, overall) + _strength(d, overall)) / 2.0
    return 1.0 / (1.0 + math.exp(-7.0 * (left - right)))


def _single_schedule(matches):
    counts = Counter()
    for match in matches:
        for game in match["games"]:
            if game["game_type"] != "singles" or game["sequence"] is None:
                continue
            seq = int(game["sequence"])
            if not 1 <= seq <= 14 or seq in (5, 10):
                continue
            h, a = _position_index(game["home_position"]), _position_index(game["away_position"])
            if h is not None and a is not None:
                counts[(seq, h, a)] += 1
    schedule = []
    for seq in range(1, 15):
        if seq in (5, 10):
            continue
        candidates = [x for x in counts if x[0] == seq]
        if candidates:
            best = max(candidates, key=lambda x: counts[x])
            schedule.append((best[1], best[2]))
    return schedule[:12] if len(schedule) >= 12 else [
        (0,0),(1,1),(2,2),(3,3),(0,1),(1,0),(2,3),(3,2),(0,2),(2,0),(1,3),(3,1)
    ]


def _team_win_probability(single_probabilities, doubles):
    distribution = [1.0]
    for p in (*single_probabilities, *doubles):
        nxt = [0.0] * (len(distribution) + 1)
        for wins, mass in enumerate(distribution):
            nxt[wins] += mass * (1.0 - p)
            nxt[wins + 1] += mass * p
        distribution = nxt
    return sum(distribution[WIN_TARGET:])


def _position_variants_from_matches(matches, player_ids):
    """Return only historically observed position orders, with smoothing for unseen orders."""
    selected = set(player_ids)
    observed = Counter()
    for match in matches:
        for side in ("home", "away"):
            players = [p for p in match["players"] if p["side"] == side and p["id"] in selected]
            if len(players) != 4 or len({p["id"] for p in players}) != 4:
                continue
            order = [None] * 4
            valid = True
            for p in players:
                pos = _position_index(p["position"])
                if pos is None or order[pos] is not None:
                    valid = False
                    break
                order[pos] = p["id"]
            if valid:
                observed[tuple(order)] += 1
    if not observed:
        return [(tuple(order), 1.0 / 24.0) for order in permutations(player_ids)]
    total = sum(observed.values())
    # Keep all historically observed orders plus a tiny uniform smoothing mass.
    variants = []
    smooth = 0.05
    all_orders = list(permutations(player_ids))
    denom = total + smooth * len(all_orders)
    for order in all_orders:
        mass = observed[order] + smooth
        if observed[order] or mass > 0:
            variants.append((order, mass / denom))
    return variants


def _team_lineups(matches, team, limit=12):
    """Predict from historically observed four-player lineups only.

    This is deliberate: enumerating combinations of every player in a team can
    explode combinatorially and was the main reason the previous analysis hit
    the timeout. Exact historical lineups already contain the strongest signal.
    """
    exact = Counter()
    names = {}
    relevant = []
    dates = []
    for match in matches:
        for side, team_name in (("home", match["home_team"]), ("away", match["away_team"])):
            if team_name != team:
                continue
            players = [p for p in match["players"] if p["side"] == side and _position_index(p["position"]) is not None]
            ids = tuple(sorted({p["id"] for p in players}))
            if len(ids) != 4:
                continue
            relevant.append((match, ids))
            names.update({p["id"]: p["name"] for p in players})
            parsed = _parse_date(match["match_date"])
            if parsed:
                dates.append(parsed)
    if not relevant:
        return [], 0, names
    reference = max(dates, default=None)
    for match, ids in relevant:
        exact[ids] += _recency_weight(match["match_date"], reference)
    ranked = sorted(exact.items(), key=lambda x: (-x[1], x[0]))[:limit]
    total = sum(score for _, score in ranked)
    scenarios = []
    for lineup, score in ranked:
        scenarios.append({
            "player_ids": list(lineup),
            "players": [{"id": pid, "name": names[pid]} for pid in lineup],
            "probability": score / total
        })
    return scenarios, len(relevant), names


def analyze_lineup(own_player_ids, opponent_team, actual_opponent_ids=None, opponent_limit=12):
    started = time.monotonic()
    own = [str(pid) for pid in own_player_ids]
    if len(own) != 4 or len(set(own)) != 4:
        raise ValueError("exactly four different own_player_ids are required")
    if not opponent_team:
        raise ValueError("opponent_team is required")
    actual = None if actual_opponent_ids is None else [str(pid) for pid in actual_opponent_ids]
    if actual is not None and (len(actual) != 4 or len(set(actual)) != 4):
        raise ValueError("exactly four different actual_opponent_ids are required")

    db = SessionLocal()
    try:
        matches = _load_snapshot(db)
    finally:
        db.close()

    names, overall, h2h = _build_stats(matches)
    missing = [pid for pid in own if pid not in names]
    if missing:
        raise ValueError(f"unknown own player IDs: {missing}")
    schedule = _single_schedule(matches)

    if actual is not None:
        scenarios = [{"player_ids": actual, "probability": 1.0}]
        source = "actual"
    else:
        scenarios, _, _ = _team_lineups(matches, opponent_team, opponent_limit)
        source = "predicted"
    if not scenarios:
        return {"ok": True, "phase": "B" if actual is not None else "A", "recommendations": [],
                "warnings": ["No historical opponent lineups available for this team."]}

    relevant_ids = set(own)
    for scenario in scenarios:
        relevant_ids.update(scenario["player_ids"])
    matchup = {
        (a, b): _matchup_probability(a, b, overall, h2h)
        for a in relevant_ids for b in relevant_ids if a != b
    }

    # Position probabilities are calculated once per opponent lineup, not once per own permutation.
    scenario_rows = []
    position_cache = {}
    for scenario in scenarios:
        key = tuple(sorted(scenario["player_ids"]))
        variants = position_cache.get(key)
        if variants is None:
            variants = _position_variants_from_matches(matches, key)
            position_cache[key] = variants
        for opponent_order, position_probability in variants:
            scenario_rows.append((scenario["probability"] * position_probability, opponent_order))

    evaluated = []
    for own_order in permutations(own):
        weighted = 0.0
        for scenario_probability, opponent_order in scenario_rows:
            doubles = (
                _pair_probability(own_order[0], own_order[1], opponent_order[0], opponent_order[1], overall),
                _pair_probability(own_order[2], own_order[3], opponent_order[2], opponent_order[3], overall),
            )
            singles = [matchup.get((own_order[h], opponent_order[a]), 0.5) for h, a in schedule]
            weighted += scenario_probability * _team_win_probability(singles, doubles)
        evaluated.append({
            "own_player_ids": list(own_order),
            "players": [names[pid] for pid in own_order],
            "team_win_probability": weighted,
        })

    elapsed = time.monotonic() - started
    # The calculation is intentionally bounded only after the optimized algorithm has run.
    # Under normal imported datasets this path should complete far below this threshold.
    if elapsed > CALCULATION_BUDGET_SECONDS:
        return {"ok": False, "error": "analysis_timeout",
                "message": "Die optimierte Berechnung hat das interne Zeitlimit überschritten.",
                "elapsed_seconds": round(elapsed, 3)}

    evaluated.sort(key=lambda x: (-x["team_win_probability"], x["own_player_ids"]))
    for rank, item in enumerate(evaluated, 1):
        item["rank"] = rank
        item["team_win_probability"] = round(item["team_win_probability"], 6)

    return {
        "ok": True,
        "phase": "B" if actual is not None else "A",
        "opponent_team": opponent_team,
        "own_player_ids": own,
        "opponent_set_source": source,
        "recommendation": evaluated[0],
        "recommendations": evaluated,
        "opponent_predictions": scenarios,
        "model": {
            "version": "strength-h2h-doubles-v5-observed-lineups",
            "win_target": WIN_TARGET,
            "single_games": MAX_SINGLE_GAMES,
            "doubles_games": DOUBLES_GAMES,
            "opponent_lineups": "recency-weighted observed four-player lineups",
            "position_model": "historically observed orders plus smoothing",
        },
        "data_quality": {
            "historical_matches": len(matches),
            "historical_matchup_pairs": len(h2h),
            "scenarios_evaluated": len(scenario_rows),
            "database_queries": 3,
            "elapsed_seconds": round(elapsed, 3),
        },
    }
