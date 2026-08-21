from __future__ import annotations

import re
import urllib.request
from collections import defaultdict
from datetime import date, timedelta
from itertools import combinations

from sqlalchemy import text, bindparam

from .db import SessionLocal

STATS_YEARS = 3


def _norm_name(value: str) -> str:
    value = (value or "").lower()
    value = value.replace("ä", "a").replace("ö", "o").replace("ü", "u").replace("ß", "ss")
    return re.sub(r"[^a-z0-9]+", "", value)


def _pair_key(a: str, b: str) -> tuple[str, str]:
    a, b = str(a), str(b)
    return tuple(sorted([a, b]))


def _split_score(stats: dict, pair: tuple[str, str]) -> tuple[int, float]:
    row = stats.get(pair, {})
    games = int(row.get("games") or 0)
    wins = int(row.get("wins") or 0)
    rate = (wins + 1.5) / (games + 3.0) if games else 0.5
    return games, rate


def _three_splits(player_ids: list[str]) -> list[tuple[tuple[str, str], tuple[str, str]]]:
    ids = [str(x) for x in player_ids]
    if len(ids) != 4:
        return []
    a, b, c, d = ids
    return [
        (_pair_key(a, b), _pair_key(c, d)),
        (_pair_key(a, c), _pair_key(b, d)),
        (_pair_key(a, d), _pair_key(b, c)),
    ]


def _players_in_pair_label(label: str, id_to_name: dict[str, str]) -> tuple[str, str] | None:
    if not label:
        return None
    norm_label = _norm_name(label)
    hits = []
    for pid, name in id_to_name.items():
        parts = [_norm_name(p) for p in re.split(r"[\s/]+", name) if _norm_name(p)]
        if any(part and part in norm_label for part in parts if len(part) >= 4):
            hits.append(pid)
        elif _norm_name(name) and _norm_name(name) in norm_label:
            hits.append(pid)
    hits = list(dict.fromkeys(hits))
    if len(hits) >= 2:
        return _pair_key(hits[0], hits[1])
    return None


def load_doubles_pair_stats(db, player_ids: list[str], team: str | None = None, ref_date: date | None = None) -> dict[tuple[str, str], dict]:
    ids = [str(x) for x in player_ids]
    if len(ids) < 2:
        return {}
    if ref_date is None:
        ref_row = db.execute(text(
            "SELECT max(to_date(substring(match_date from 1 for 10), 'DD.MM.YYYY')) FROM xttv_matches WHERE match_date IS NOT NULL"
        )).scalar()
        ref_date = ref_row or date.today()
    cutoff = ref_date - timedelta(days=int(round(STATS_YEARS * 365.25)))

    params: dict = {"ids": ids, "cutoff": cutoff}
    team_clause = ""
    if team:
        params["team"] = team
        team_clause = "AND (m.home_team = :team OR m.away_team = :team)"

    rows = db.execute(
        text(f"""
            SELECT g.match_id,
                   g.sequence,
                   g.home_player,
                   g.away_player,
                   g.result,
                   mp.external_player_id::text AS player_id,
                   mp.name,
                   mp.side
            FROM match_games g
            JOIN xttv_matches m ON m.id = g.match_id
            JOIN match_players mp ON mp.match_id = m.id
            WHERE g.game_type = 'doubles'
              AND mp.external_player_id::text IN :ids
              AND to_date(substring(m.match_date from 1 for 10), 'DD.MM.YYYY') >= :cutoff
              {team_clause}
        """).bindparams(bindparam("ids", expanding=True)),
        params,
    ).mappings()

    stats: dict[tuple[str, str], dict] = defaultdict(lambda: {"wins": 0, "games": 0, "game5": 0, "game10": 0})
    by_match: dict[int, dict] = defaultdict(lambda: {"games": [], "players": defaultdict(list)})
    for r in rows:
        mid = int(r["match_id"])
        by_match[mid]["players"][r["side"]].append({"id": str(r["player_id"]), "name": r["name"]})
        game = {
            "sequence": int(r["sequence"] or 0),
            "home_player": r["home_player"] or "",
            "away_player": r["away_player"] or "",
            "result": r["result"] or "",
        }
        if game not in by_match[mid]["games"]:
            by_match[mid]["games"].append(game)

    for payload in by_match.values():
        for game in payload["games"]:
            for side in ("home", "away"):
                players = payload["players"].get(side, [])
                side_ids = {p["id"] for p in players if p["id"] in ids}
                if len(side_ids) < 2:
                    continue
                pair_label = game["home_player"] if side == "home" else game["away_player"]
                id_to_side = {p["id"]: p["name"] for p in players if p["id"] in ids}
                detected = _players_in_pair_label(pair_label, id_to_side)
                if detected is None and len(side_ids) == 2:
                    detected = _pair_key(*side_ids)
                if detected is None or not set(detected).issubset(set(ids)):
                    continue
                win = _side_won(game["result"], side)
                stats[detected]["games"] += 1
                stats[detected]["wins"] += int(win)
                if game["sequence"] == 5:
                    stats[detected]["game5"] += 1
                elif game["sequence"] == 10:
                    stats[detected]["game10"] += 1

    return dict(stats)


def _side_won(result: str, side: str) -> bool:
    match = re.fullmatch(r"\s*(\d+)\s*:\s*(\d+)\s*", result or "")
    if not match:
        return False
    left, right = int(match.group(1)), int(match.group(2))
    if side == "home":
        return left > right
    return right > left


def suggest_pairs(player_ids: list[str], team: str | None = None, league: str | None = None) -> dict:
    ids = [str(x) for x in player_ids]
    if len(ids) != 4 or len(set(ids)) != 4:
        return {"ok": False, "error": "exactly four player ids required", "pairs": []}

    with SessionLocal() as db:
        stats = load_doubles_pair_stats(db, ids, team=team)

    split_options = []
    for pair_a, pair_b in _three_splits(ids):
        ga, ra = _split_score(stats, pair_a)
        gb, rb = _split_score(stats, pair_b)
        split_options.append({
            "pair_a": list(pair_a),
            "pair_b": list(pair_b),
            "pair_a_record": {"wins": stats.get(pair_a, {}).get("wins", 0), "games": ga},
            "pair_b_record": {"wins": stats.get(pair_b, {}).get("wins", 0), "games": gb},
            "pair_a_win_rate": round(ra, 3) if ga else None,
            "pair_b_win_rate": round(rb, 3) if gb else None,
        })

    all_pairs = [_pair_key(a, b) for a, b in combinations(ids, 2)]
    best_pair = max(all_pairs, key=lambda pair: _split_score(stats, pair)[0])
    rest = [pid for pid in ids if pid not in best_pair]
    pair_a = best_pair
    pair_b = _pair_key(rest[0], rest[1])
    ga, ra = _split_score(stats, pair_a)
    gb, rb = _split_score(stats, pair_b)
    if ga + gb == 0:
        scored = max(split_options, key=lambda row: (row["pair_a_record"]["games"] + row["pair_b_record"]["games"], (row["pair_a_win_rate"] or 0) + (row["pair_b_win_rate"] or 0)))
        pair_a = tuple(scored["pair_a"])
        pair_b = tuple(scored["pair_b"])
        ga = scored["pair_a_record"]["games"]
        gb = scored["pair_b_record"]["games"]
        ra = scored["pair_a_win_rate"] or 0.5
        rb = scored["pair_b_win_rate"] or 0.5

    return {
        "ok": True,
        "suggested_pair_a": list(pair_a),
        "suggested_pair_b": list(pair_b),
        "suggested_stronger_pair": 1 if ra >= rb else 2,
        "pairs": split_options,
        "stats_years": STATS_YEARS,
        "source": "doubles_match_history",
    }


def pair_doubles_win_rate(pair: tuple[str, str], stats: dict) -> float | None:
    row = stats.get(pair)
    if not row or int(row.get("games") or 0) < 1:
        return None
    wins, games = int(row["wins"]), int(row["games"])
    return (wins + 1.5) / (games + 3.0)


def choose_opponent_doubles_pairs(opp_ids: list[str], stats: dict, fallback_profiles: dict, combined_strength_fn) -> tuple[tuple[str, str], tuple[str, str]]:
    splits = _three_splits(opp_ids)
    if not splits:
        ranked = sorted(opp_ids, key=lambda pid: combined_strength_fn(fallback_profiles.get(pid, {})), reverse=True)
        return (tuple(ranked[:2]), tuple(ranked[2:4]))

    def split_quality(pair_a, pair_b):
        ga, ra = _split_score(stats, pair_a)
        gb, rb = _split_score(stats, pair_b)
        if ga + gb == 0:
            sa = combined_strength_fn(fallback_profiles.get(pair_a[0], {})) + combined_strength_fn(fallback_profiles.get(pair_a[1], {}))
            sb = combined_strength_fn(fallback_profiles.get(pair_b[0], {})) + combined_strength_fn(fallback_profiles.get(pair_b[1], {}))
            return (0, sa / 2.0, sb / 2.0)
        return (ga + gb, ra, rb)

    best = max(splits, key=lambda s: split_quality(s[0], s[1]))
    pair_a, pair_b = best
    _, ra, rb = split_quality(pair_a, pair_b)
    if ra >= rb:
        return pair_a, pair_b
    return pair_b, pair_a


def choose_opponent_doubles_on_games(opp_ids: list[str], stats: dict, profiles: dict, combined_strength_fn) -> tuple[tuple[str, str], tuple[str, str]]:
    """Return (opp pair on game 5, opp pair on game 10) using doubles history."""
    strong, weak = choose_opponent_doubles_pairs(opp_ids, stats, profiles, combined_strength_fn)
    row = stats.get(_pair_key(*strong), {})
    game5 = int(row.get("game5") or 0)
    game10 = int(row.get("game10") or 0)
    if game10 > game5:
        return weak, strong
    return strong, weak


def opponent_doubles_placement_mixtures(
    opp_ids: list[str], stats: dict, profiles: dict, combined_strength_fn,
) -> list[tuple[float, tuple[str, str], tuple[str, str]]]:
    """Weighted opponent doubles placements: (weight, pair on game 5, pair on game 10)."""
    strong, weak = choose_opponent_doubles_pairs(opp_ids, stats, profiles, combined_strength_fn)
    row = stats.get(_pair_key(*strong), {})
    game5 = int(row.get("game5") or 0)
    game10 = int(row.get("game10") or 0)
    total = game5 + game10
    if total <= 0:
        return [(1.0, strong, weak)]
    mixtures: list[tuple[float, tuple[str, str], tuple[str, str]]] = []
    if game5 > 0:
        mixtures.append((game5 / total, strong, weak))
    if game10 > 0:
        mixtures.append((game10 / total, weak, strong))
    return mixtures or [(1.0, strong, weak)]


def predict_opponent_doubles_lineup(
    opp_ids: list[str], stats: dict, profiles: dict, combined_strength_fn,
) -> dict:
    """Most likely opponent doubles pairs on games 5 and 10 from history."""
    strong, weak = choose_opponent_doubles_pairs(opp_ids, stats, profiles, combined_strength_fn)
    game5, game10 = choose_opponent_doubles_on_games(opp_ids, stats, profiles, combined_strength_fn)
    mixtures = opponent_doubles_placement_mixtures(opp_ids, stats, profiles, combined_strength_fn)
    strong_on_10 = sum(weight for weight, _, game10_pair in mixtures if _pair_key(*game10_pair) == _pair_key(*strong))
    return {
        'game5': list(game5),
        'game10': list(game10),
        'pair_strong': list(strong),
        'pair_weak': list(weak),
        'strong_on_game10_probability': round(strong_on_10, 6),
    }


def doubles_matchup_probability(own_pair, opp_pair, stats, profiles, combined_strength_fn, logistic_fn):
    own_rate = pair_doubles_win_rate(_pair_key(*own_pair), stats)
    opp_rate = pair_doubles_win_rate(_pair_key(*opp_pair), stats)
    if own_rate is not None and opp_rate is not None:
        return logistic_fn(own_rate - opp_rate, scale=3.0)
    left = (combined_strength_fn(profiles.get(own_pair[0], {})) + combined_strength_fn(profiles.get(own_pair[1], {}))) / 2.0
    right = (combined_strength_fn(profiles.get(opp_pair[0], {})) + combined_strength_fn(profiles.get(opp_pair[1], {}))) / 2.0
    return logistic_fn(left - right)
