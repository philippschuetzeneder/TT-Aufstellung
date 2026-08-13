from __future__ import annotations

from .opponent_prediction_service import predict_opponent_lineups


def get_opponent_predictions(team: str, limit: int = 10) -> dict:
    return predict_opponent_lineups(team, limit=limit)
