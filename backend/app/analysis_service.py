from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime
from itertools import combinations, permutations
import math
import re

from sqlalchemy.orm import selectinload

from .db import SessionLocal, create_all
from .models import MatchGame, MatchPlayer, XttvMatch
from .opponent_prediction_service import predict_opponent_lineups


MAX_SINGLE_GAMES = 12
DOUBLES_GAMES = 2
WIN_TARGET = 8


def _key(player: MatchPlayer) -> str:
    return str(player.external_player_id or f"name:{player.name}")


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M"):
        try:
            return datetime.strptime(value[:16], fmt).date()
        except ValueError:
            pass
    return None


def _position_index(position: str | None) -> int | None:
    if not position:
        return None
    value = position.strip().upper()
    if value in "ABCD":
        return ord(value) - ord("A")
    if value in {"1", "2", "3", "4"}:
        return int(value) - 1
    return None


def _score(result: str | None) -> tuple[int, int] | None:
    match = re.fullmatch(r"\s*(\d+)\s*:\s*(\d+)\s*", result or "")
    return (int(match.group(1)), int(match.group(2))) if match else None


def _load_matches(db):
    return (
        db.query(XttvMatch)
        .options(selectinload(XttvMatch.players), selectinload(XttvMatch.games))
        .order_by(XttvMatch.match_date, XttvMatch.id)
        .all()
    )


def _matchup_rates(matches: list[XttvMatch]) -> dict[tuple[str, str], tuple[int, int]]:
    """Return (wins, matches) for every ordered player-v-player pair."""
    rates: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    for match in matches:
        by_pos = {(p.side, p.position): p for p in match.players}
        for game in match.games:
            if game.game_type != "singles":
                continue
            score = _score(game.result)
            if not score:
                continue
            home = by_pos.get(("home", game.home_position))
            away = by_pos.get(("away", game.away_position))
            if not home or not away or score[0] == score[1]:
                continue
            home_id, away_id = _key(home), _key(away)
            rates[(home_id, away_id)][1] += 1
            rates[(away_id, home_id)][1] += 1
            if score[0] > score[1]:
                rates[(home_id, away_id)][0] += 1
            else:
                rates[(away_id, home_id)][0] += 1
    return {pair: (values[0], values[1]) for pair, values in rates.items()}


def _matchup_probability(a: str, b: str, rates: dict[tuple[str, str], tuple[int, int]]) -> float:
    wins, matches = rates.get((a, b), (0, 0))
    # Laplace smoothing keeps unseen and tiny samples usable without creating
    # extreme 0/100% probabilities from one historical game.
    return (wins + 1.0) / (matches + 2.0)


def _position_variants(
    matches: list[XttvMatch],
    player_ids: tuple[str, ...],
    limit: int = 24,
) -> list[dict]:
    """Build a probability distribution over the 24 hidden permutations."""
    selected = set(player_ids)
    position_counts: Counter[tuple[str, int]] = Counter()
    player_counts: Counter[str] = Counter()

    for match in matches:
        for side in ("home", "away"):
            players = [p for p in match.players if p.side == side and _key(p) in selected]
            if len(players) != 4 or len({_key(p) for p in players}) != 4:
                continue
            for p in players:
                idx = _position_index(p.position)
                if idx is not None:
                    position_counts[(_key(p), idx)] += 1
                    player_counts[_key(p)] += 1

    scored = []
    for order in permutations(player_ids):
        score = 1.0
        for idx, player_id in enumerate(order):
            numerator = position_counts[(player_id, idx)] + 0.5
            denominator = player_counts[player_id] + 2.0
            score *= numerator / denominator
        scored.append((order, score))

    total = sum(score for _, score in scored)
    if total == 0:
        total = float(len(scored))
        scored = [(order, 1.0) for order, _ in scored]

    return [
        {"player_ids": list(order), "probability": score / total}
        for order, score in sorted(scored, key=lambda x: (-x[1], x[0]))[:limit]
    ]


def _single_schedule(matches: list[XttvMatch]) -> list[tuple[int, int]]:
    """Infer the fixed 12-single position schedule from imported reports."""
    counts: Counter[tuple[int, int, int]] = Counter()
    for match in matches:
        for game in match.games:
            if game.game_type != "singles" or game.sequence is None:
                continue
            h, a = _position_index(game.home_position), _position_index(game.away_position)
            if h is None or a is None or not 1 <= int(game.sequence) <= 14:
                continue
            counts[(int(game.sequence), h, a)] += 1

    schedule: dict[int, tuple[int, int]] = {}
    for seq in range(1, 15):
        candidates = [item for item in counts if item[0] == seq and item[1:] is not None]
        if not candidates:
            continue
        best = max(candidates, key=lambda item: counts[item])
        # XTTV games 5 and 10 are doubles in this format.
        if seq not in (5, 10):
            schedule[seq] = (best[1], best[2])

    # A validated report has exactly 12 singles. If historical data is sparse,
    # fall back to a deterministic 12-pair round-robin schedule.
    if len(schedule) < MAX_SINGLE_GAMES:
        fallback = [
            (0, 0), (1, 1), (2, 2), (3, 3),
            (0, 1), (1, 0), (2, 3), (3, 2),
            (0, 2), (2, 0), (1, 3), (3, 1),
        ]
        return fallback
    return [schedule[seq] for seq in sorted(schedule)[:MAX_SINGLE_GAMES]]


def _total_win_probability(single_probabilities: list[float], doubles_probability: float = 0.5) -> float:
    """Probability of reaching eight wins in a 12-single + 2-double match.

    The actual match stops as soon as one side reaches eight wins. Because the
    maximum is 14 games, the winner event is equivalent to having at least eight
    wins in the full latent 14-game schedule. A 7:7 result is the only tie case.
    """
    probabilities = list(single_probabilities) + [doubles_probability] * DOUBLES_GAMES
    distribution = [1.0]
    for probability in probabilities:
        next_distribution = [0.0] * (len(distribution) + 1)
        for wins, mass in enumerate(distribution):
            next_distribution[wins] += mass * (1.0 - probability)
            next_distribution[wins + 1] += mass * probability
        distribution = next_distribution
    return sum(distribution[WIN_TARGET:])


def _scenario_probability(
    own_order: tuple[str, ...],
    opponent_order: tuple[str, ...],
    schedule: list[tuple[int, int]],
    rates: dict[tuple[str, str], tuple[int, int]],
) -> float:
    single_probabilities = [
        _matchup_probability(own_order[h], opponent_order[a], rates)
        for h, a in schedule
    ]
    return _total_win_probability(single_probabilities)


def analyze_lineup(
    own_player_ids: list[str],
    opponent_team: str,
    actual_opponent_ids: list[str] | None = None,
    opponent_limit: int = 25,
) -> dict:
    """Evaluate all 24 own orders against predicted or known opponents."""
    own = [str(pid) for pid in own_player_ids]
    if len(own) != 4 or len(set(own)) != 4:
        raise ValueError("exactly four different own_player_ids are required")
    if not opponent_team:
        raise ValueError("opponent_team is required")
    if actual_opponent_ids is not None:
        actual = [str(pid) for pid in actual_opponent_ids]
        if len(actual) != 4 or len(set(actual)) != 4:
            raise ValueError("exactly four different actual_opponent_ids are required")
    else:
        actual = None

    create_all()
    db = SessionLocal()
    try:
        matches = _load_matches(db)
        known_ids = {_key(p): p.name for match in matches for p in match.players}
        missing_own = [pid for pid in own if pid not in known_ids]
        if missing_own:
            raise ValueError(f"unknown own player IDs: {missing_own}")

        rates = _matchup_rates(matches)
        schedule = _single_schedule(matches)

        if actual is not None:
            scenarios = [{"player_ids": actual, "probability": 1.0}]
            position_scenarios = _position_variants(matches, tuple(actual))
            opponent_set_source = "actual"
        else:
            prediction = predict_opponent_lineups(opponent_team, limit=opponent_limit)
            scenarios = prediction.get("predictions", [])
            position_scenarios = None
            opponent_set_source = "predicted"

        if not scenarios:
            return {
                "ok": True,
                "phase": "B" if actual is not None else "A",
                "recommendations": [],
                "warnings": ["No historical opponent lineups available for this team."],
            }

        evaluated = []
        scenario_count = 0
        for own_order in permutations(own):
            weighted_probability = 0.0
            for scenario in scenarios:
                opponent_ids = tuple(scenario["player_ids"])
                set_probability = float(scenario.get("probability", 1.0))
                variants = _position_variants(matches, opponent_ids)
                for variant in variants:
                    position_probability = float(variant["probability"])
                    weighted_probability += (
                        set_probability
                        * position_probability
                        * _scenario_probability(own_order, tuple(variant["player_ids"]), schedule, rates)
                    )
                    scenario_count += 1
            evaluated.append({
                "own_player_ids": list(own_order),
                "own_players": [{"id": pid, "name": known_ids[pid]} for pid in own_order],
                "win_probability": weighted_probability,
            })

        evaluated.sort(key=lambda item: (-item["win_probability"], item["own_player_ids"]))
        for rank, item in enumerate(evaluated, 1):
            item["rank"] = rank
            item["win_probability"] = round(item["win_probability"], 6)
            item["loss_probability"] = round(1.0 - item["win_probability"], 6)

        best = evaluated[0]
        return {
            "ok": True,
            "phase": "B" if actual is not None else "A",
            "opponent_team": opponent_team,
            "own_player_ids": own,
            "opponent_set_source": opponent_set_source,
            "recommendation": best,
            "recommendations": evaluated,
            "opponent_predictions": scenarios if actual is None else [{"player_ids": actual, "probability": 1.0}],
            "opponent_position_variants": position_scenarios if actual is not None else None,
            "model": {
                "version": "matchup-and-lineup-baseline-v1",
                "win_target": WIN_TARGET,
                "single_games": MAX_SINGLE_GAMES,
                "doubles_games": DOUBLES_GAMES,
                "doubles_probability": 0.5,
                "single_schedule_pairs": schedule,
                "unseen_matchup_probability": 0.5,
            },
            "data_quality": {
                "historical_matches": len(matches),
                "historical_matchup_pairs": len(rates),
                "scenarios_evaluated": scenario_count,
                "note": "Doubles are currently modeled as 50/50; historical doubles modeling is a later refinement.",
            },
        }
    finally:
        db.close()
