from __future__ import annotations

import re

from sqlalchemy.orm import selectinload

from .db import SessionLocal, create_all
from .models import MatchGame, XttvMatch

_VALID_TEAM_RESULTS = {"10:0", "0:10", "9:1", "1:9", "8:2", "2:8", "8:3", "3:8", "8:4", "4:8", "8:5", "5:8", "8:6", "6:8", "7:7"}

def _expected_singles_count(result: str) -> int | None:
    m = re.fullmatch(r"(\d+)\s*:\s*(\d+)", result or "")
    if not m: return None
    a, b = int(m.group(1)), int(m.group(2))
    high, low = max(a, b), min(a, b)
    if high == 8 and low in (2, 3, 4, 5, 6): return low + 6
    if high == 9 and low == 1: return 8
    if high == 10 and low == 0: return 8
    if (a, b) == (7, 7): return 12
    return None

def _winner(game: MatchGame) -> str | None:
    m = re.fullmatch(r"(\d+)\s*:\s*(\d+)", game.result or "")
    if not m: return None
    h, a = int(m.group(1)), int(m.group(2))
    if h == a: return None
    return "home" if h > a else "away"

def validate_database() -> dict:
    create_all()
    db = SessionLocal()
    try:
        matches = db.query(XttvMatch).options(selectinload(XttvMatch.players), selectinload(XttvMatch.games)).order_by(XttvMatch.id).all()
        issues = []
        player_count_under_8 = 0
        for match in matches:
            prefix = {"match_id": match.id, "meid": match.external_id}
            players, games = list(match.players), list(match.games)
            if len(players) < 8:
                player_count_under_8 += 1
            else:
                if len(players) != 8:
                    issues.append({**prefix, "type": "player_count", "message": f"expected 8 players, found {len(players)}"})
                expected_home = {"A", "B", "C", "D"} if match.home_scheme == "letters" else {"1", "2", "3", "4"}
                expected_away = {"1", "2", "3", "4"} if match.away_scheme == "numbers" else {"A", "B", "C", "D"}
                for side, expected in (("home", expected_home), ("away", expected_away)):
                    side_players = [p for p in players if p.side == side]
                    positions = {p.position for p in side_players}
                    if positions != expected:
                        issues.append({**prefix, "type": "player_positions", "side": side, "message": f"expected positions {sorted(expected)}, found {sorted(positions)}"})
                    ids = [p.external_player_id for p in side_players]
                    if len(ids) != len(set(ids)):
                        issues.append({**prefix, "type": "duplicate_player_id", "side": side, "message": "duplicate external_player_id"})
            result = (match.team_result or "").replace(" ", "")
            if result not in _VALID_TEAM_RESULTS:
                issues.append({**prefix, "type": "team_result", "message": f"invalid team result {match.team_result!r}"})
            expected_singles = _expected_singles_count(result)
            singles = [g for g in games if g.game_type == "singles"]
            doubles = [g for g in games if g.game_type == "doubles"]
            if len(doubles) != 2:
                issues.append({**prefix, "type": "double_count", "message": f"expected 2 doubles, found {len(doubles)}"})
            if expected_singles is not None and len(singles) != expected_singles:
                issues.append({**prefix, "type": "single_count", "message": f"result {result} requires {expected_singles} singles, found {len(singles)}"})
            sequences = [g.sequence for g in games]
            if len(sequences) != len(set(sequences)):
                issues.append({**prefix, "type": "duplicate_sequence", "message": f"duplicate game sequence numbers: {sequences}"})
            if {g.sequence for g in doubles} != {5, 10}:
                issues.append({**prefix, "type": "double_sequences", "message": f"expected doubles at sequences 5 and 10, found {sorted(g.sequence for g in doubles)}"})
            for game in games:
                if _winner(game) is None:
                    issues.append({**prefix, "type": "game_result", "sequence": game.sequence, "message": f"invalid or tied game result {game.result!r}"})
            home_wins = sum(_winner(g) == "home" for g in games)
            away_wins = sum(_winner(g) == "away" for g in games)
            if expected_singles is not None and f"{home_wins}:{away_wins}" != result:
                issues.append({**prefix, "type": "score_mismatch", "message": f"team result is {result}, games add up to {home_wins}:{away_wins}"})
        issue_match_ids = {i["match_id"] for i in issues}
        return {"ok": not issues, "matches_checked": len(matches), "matches_valid": len(matches) - len(issue_match_ids), "matches_with_issues": len(issue_match_ids), "player_count_under_8": player_count_under_8, "issues_count": len(issues), "issues": issues}
    finally:
        db.close()
