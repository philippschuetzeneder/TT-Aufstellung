from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from itertools import combinations
import math

from sqlalchemy.orm import selectinload

from .db import SessionLocal, create_all
from .models import MatchPlayer, XttvMatch

RECENCY_HALF_LIFE_DAYS = 45.0
SMOOTHING = 0.25


def _player_key(player: MatchPlayer) -> str:
    return str(player.external_player_id or f"name:{player.name}")


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M"):
        try:
            return datetime.strptime(value[:16], fmt).date()
        except ValueError:
            continue
    return None


def _recency_weight(match: XttvMatch, reference_date: date | None) -> float:
    match_date = _parse_date(match.match_date)
    if not match_date or not reference_date:
        return 1.0
    age = max(0, (reference_date - match_date).days)
    return math.pow(0.5, age / RECENCY_HALF_LIFE_DAYS)


def predict_opponent_lineups(team: str, limit: int = 10, reference_date: date | None = None) -> dict:
    """Predict the opponent's four-player set from historical team lineups.

    This first production baseline predicts the *set* of four players, not their
    hidden positions. It uses recency-weighted historical lineup usage with
    smoothing over all four-player combinations. The output is therefore a
    distribution over complete sets rather than four independent probabilities.
    """
    if not team:
        raise ValueError("team is required")
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100")

    create_all()
    db = SessionLocal()
    try:
        matches = (
            db.query(XttvMatch)
            .options(selectinload(XttvMatch.players))
            .order_by(XttvMatch.match_date, XttvMatch.id)
            .all()
        )

        relevant = []
        player_names: dict[str, str] = {}
        for match in matches:
            for side, team_name in (("home", match.home_team), ("away", match.away_team)):
                if team_name != team:
                    continue
                players = [
                    p for p in match.players
                    if p.side == side and p.position in {"A", "B", "C", "D", "1", "2", "3", "4"}
                ]
                keys = tuple(sorted({_player_key(p) for p in players}))
                if len(keys) != 4:
                    continue
                for p in players:
                    player_names[_player_key(p)] = p.name
                relevant.append((match, keys))

        if not relevant:
            return {
                "ok": True,
                "team": team,
                "matches": 0,
                "predictions": [],
                "players": [],
                "model": "historical-lineup-frequency-v1",
            }

        if reference_date is None:
            parsed_dates = [_parse_date(match.match_date) for match, _ in relevant]
            reference_date = max((d for d in parsed_dates if d), default=None)

        exact_weighted = Counter()
        player_weighted = Counter()
        pair_weighted = Counter()
        total_weight = 0.0
        for match, keys in relevant:
            weight = _recency_weight(match, reference_date)
            total_weight += weight
            exact_weighted[keys] += weight
            for key in keys:
                player_weighted[key] += weight
            for pair in combinations(keys, 2):
                pair_weighted[pair] += weight

        candidates = sorted(player_names)
        combinations_all = list(combinations(candidates, 4))
        if not combinations_all:
            return {
                "ok": True,
                "team": team,
                "matches": len(relevant),
                "predictions": [],
                "players": candidates,
                "model": "historical-lineup-frequency-v1",
            }

        scores = []
        for lineup in combinations_all:
            exact = exact_weighted[lineup]
            inclusion = sum(player_weighted[p] / total_weight for p in lineup) / 4
            cohesion = sum(
                pair_weighted[pair] / total_weight
                for pair in combinations(lineup, 2)
            ) / 6
            if exact > 0:
                score = exact + SMOOTHING * (0.5 * inclusion + 0.5 * cohesion)
            else:
                score = SMOOTHING * (0.55 * inclusion + 0.45 * cohesion)
            scores.append((lineup, score, exact, inclusion, cohesion))

        score_total = sum(item[1] for item in scores)
        predictions = []
        for lineup, score, exact, inclusion, cohesion in sorted(
            scores, key=lambda item: (-item[1], item[0])
        )[:limit]:
            predictions.append({
                "player_ids": list(lineup),
                "players": [{"id": pid, "name": player_names[pid]} for pid in lineup],
                "probability": round(score / score_total, 6) if score_total else 0.0,
                "historical_weight": round(exact, 6),
                "player_inclusion": round(inclusion, 6),
                "pair_cohesion": round(cohesion, 6),
            })

        return {
            "ok": True,
            "team": team,
            "matches": len(relevant),
            "candidate_players": len(candidates),
            "reference_date": reference_date.isoformat() if reference_date else None,
            "total_probability": 1.0,
            "top_probability": round(sum(item["probability"] for item in predictions), 6),
            "predictions": predictions,
            "model": "historical-lineup-frequency-v1",
            "parameters": {
                "recency_half_life_days": RECENCY_HALF_LIFE_DAYS,
                "smoothing": SMOOTHING,
                "all_combination_count": len(combinations_all),
            },
        }
    finally:
        db.close()
