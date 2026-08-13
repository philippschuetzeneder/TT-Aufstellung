from __future__ import annotations

from .analysis_service import analyze_lineup


def run_analysis(own_player_ids, opponent_team, actual_opponent_ids=None, opponent_limit=25):
    return analyze_lineup(
        own_player_ids=own_player_ids,
        opponent_team=opponent_team,
        actual_opponent_ids=actual_opponent_ids,
        opponent_limit=opponent_limit,
    )
