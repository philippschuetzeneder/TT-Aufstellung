from __future__ import annotations

from collections import Counter

from .analytics_service import _load_matches, _player_key, _score, lineup_stats, matchup_matrix, player_stats
from .db import SessionLocal, create_all


def validate_analytics() -> dict:
    create_all()
    db = SessionLocal()
    issues = []
    try:
        matches = _load_matches(db)
        player_singles = Counter()
        player_wins = Counter()
        player_losses = Counter()
        matchup_pairs = Counter()
        lineup_entries = 0
        singles_games = 0
        doubles_games = 0
        for match in matches:
            by_pos = {(p.side, p.position): p for p in match.players}
            for side in ("home", "away"):
                players = [p for p in match.players if p.side == side]
                if players and len(players) != 4:
                    issues.append({"type": "lineup_player_count", "match_id": match.id, "side": side, "count": len(players)})
                if len(players) == 4:
                    lineup_entries += 1
                    positions = [p.position for p in players]
                    keys = [_player_key(p) for p in players]
                    if len(set(positions)) != 4:
                        issues.append({"type": "duplicate_position", "match_id": match.id, "side": side, "positions": positions})
                    if len(set(keys)) != 4:
                        issues.append({"type": "duplicate_player", "match_id": match.id, "side": side, "players": keys})
            for game in match.games:
                if game.game_type == "doubles":
                    doubles_games += 1
                    continue
                if game.game_type != "singles":
                    continue
                singles_games += 1
                hp = by_pos.get(("home", game.home_position))
                ap = by_pos.get(("away", game.away_position))
                score = _score(game.result)
                if not hp or not ap or not score or score[0] == score[1]:
                    issues.append({"type": "invalid_singles_game", "match_id": match.id, "game_id": game.id, "result": game.result})
                    continue
                h, a = _player_key(hp), _player_key(ap)
                player_singles[h] += 1
                player_singles[a] += 1
                matchup_pairs[(h, a)] += 1
                if score[0] > score[1]:
                    player_wins[h] += 1; player_losses[a] += 1
                else:
                    player_wins[a] += 1; player_losses[h] += 1
            singles_count = sum(g.game_type == "singles" for g in match.games)
            doubles_count = sum(g.game_type == "doubles" for g in match.games)
            if doubles_count != 2:
                issues.append({"type": "doubles_count", "match_id": match.id, "external_match_id": match.external_id, "count": doubles_count})
            if singles_count < 8:
                issues.append({"type": "singles_below_minimum", "match_id": match.id, "external_match_id": match.external_id, "count": singles_count})
        players_report = player_stats()["players"]
        players_by_id = {_player_key_from_dict(p): p for p in players_report}
        for pid, expected in player_singles.items():
            actual = players_by_id.get(pid)
            if not actual:
                issues.append({"type": "player_missing", "player_id": pid}); continue
            if (actual["singles"], actual["singles_wins"], actual["singles_losses"]) != (expected, player_wins[pid], player_losses[pid]):
                issues.append({"type": "player_stats_mismatch", "player_id": pid})
        lineup_report = lineup_stats()
        actual_lineups = sum(x["count"] for x in lineup_report["lineups"])
        if actual_lineups != lineup_entries:
            issues.append({"type": "lineup_aggregate_mismatch", "expected": lineup_entries, "actual": actual_lineups})
        if lineup_entries != len(matches) * 2:
            issues.append({"type": "lineup_match_coverage", "expected": len(matches) * 2, "actual": lineup_entries})
        matrix_report = matchup_matrix()["matchups"]
        matrix = {(x["player_id"], x["opponent_id"]): x for x in matrix_report}
        for a, b in matchup_pairs:
            f, r = matrix.get((a, b)), matrix.get((b, a))
            if not f or not r:
                issues.append({"type": "matrix_missing_pair", "player_id": a, "opponent_id": b}); continue
            if f["matches"] != r["matches"] or f["wins"] != r["losses"] or f["losses"] != r["wins"]:
                issues.append({"type": "matrix_reverse_mismatch", "player_id": a, "opponent_id": b})
        return {"ok": not issues, "matches_checked": len(matches), "players_checked": len(players_by_id), "singles_games_checked": singles_games, "doubles_games_checked": doubles_games, "lineup_entries_checked": lineup_entries, "matchup_pairs_checked": len(matchup_pairs), "matrix_rows_checked": len(matrix_report), "issues_count": len(issues), "issues": issues}
    finally:
        db.close()


def _player_key_from_dict(player: dict) -> str:
    return str(player.get("external_player_id") or f"name:{player.get('name')}")
