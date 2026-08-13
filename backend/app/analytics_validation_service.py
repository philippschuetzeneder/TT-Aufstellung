from __future__ import annotations

from .analytics_service import lineup_stats, matchup_matrix, matchup_stats, player_stats
from .db import SessionLocal, create_all
from .models import MatchPlayer, XttvMatch


def _key(player: MatchPlayer) -> str:
    return str(player.external_player_id or f"name:{player.name}")


def validate_analytics() -> dict:
    """Check consistency of the analytics used by the lineup optimizer."""
    create_all()
    db = SessionLocal()
    issues: list[dict] = []
    try:
        matches = db.query(XttvMatch).all()
        players = player_stats()
        lineups = lineup_stats()
        matchups = matchup_stats()
        matrix = matchup_matrix()

        lineup_keys = {tuple(sorted(row["players"])) for row in lineups["lineups"]}
        complete_sides = 0
        for match in matches:
            for side in ("home", "away"):
                ps = [p for p in match.players if p.side == side]
                if len(ps) == 4:
                    complete_sides += 1
                    key = tuple(sorted(_key(p) for p in ps))
                    if key not in lineup_keys:
                        issues.append({"type": "lineup_missing", "match_id": match.id, "side": side})

        if lineups["matches"] != complete_sides:
            issues.append({"type": "lineup_appearance_count", "expected": complete_sides, "actual": lineups["matches"]})

        probability_sum = sum(row["probability"] or 0 for row in lineups["lineups"])
        if lineups["matches"] and abs(probability_sum - 1.0) > 0.005:
            issues.append({"type": "lineup_probability_sum", "value": round(probability_sum, 6)})

        expected_matrix = {}
        for row in matchups["matchups"]:
            forward_key = (row["home_player_id"], row["away_player_id"])
            reverse_key = (row["away_player_id"], row["home_player_id"])
            forward = expected_matrix.setdefault(forward_key, {"matches": 0, "wins": 0, "losses": 0})
            reverse = expected_matrix.setdefault(reverse_key, {"matches": 0, "wins": 0, "losses": 0})
            forward["matches"] += row["matches"]
            forward["wins"] += row["home_wins"]
            forward["losses"] += row["away_wins"]
            reverse["matches"] += row["matches"]
            reverse["wins"] += row["away_wins"]
            reverse["losses"] += row["home_wins"]

        matrix_by_pair = {(r["player_id"], r["opponent_id"]): r for r in matrix["matchups"]}
        for pair, expected in expected_matrix.items():
            actual = matrix_by_pair.get(pair)
            if not actual or (actual["matches"], actual["wins"], actual["losses"]) != (
                expected["matches"], expected["wins"], expected["losses"]
            ):
                issues.append({"type": "matrix_mismatch", "pair": [pair[0], pair[1]]})

        for row in matrix["matchups"]:
            if row["matches"] != row["wins"] + row["losses"]:
                issues.append({"type": "matrix_count_mismatch", "player_id": row["player_id"], "opponent_id": row["opponent_id"]})
            expected_rate = round(row["wins"] / row["matches"], 4) if row["matches"] else None
            if row["win_rate"] != expected_rate:
                issues.append({"type": "matrix_rate_mismatch", "player_id": row["player_id"], "opponent_id": row["opponent_id"]})

        return {
            "ok": not issues,
            "matches_checked": len(matches),
            "players_checked": players["count"],
            "lineup_sides_checked": complete_sides,
            "lineup_count": len(lineups["lineups"]),
            "singles_games_checked": sum(1 for m in matches for g in m.games if g.game_type == "singles"),
            "matchup_rows": matchups["count"],
            "matrix_rows": matrix["count"],
            "issues_count": len(issues),
            "issues": issues[:100],
        }
    finally:
        db.close()
