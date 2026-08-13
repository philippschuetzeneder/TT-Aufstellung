import re

from sqlalchemy.orm import selectinload

from .db import SessionLocal, create_all
from .models import MatchGame, MatchPlayer, XttvMatch


_VALID_TEAM_RESULTS = {
    "10:0",
    "0:10",
    "9:1",
    "1:9",
    "8:2",
    "2:8",
    "8:3",
    "3:8",
    "8:4",
    "4:8",
    "8:5",
    "5:8",
    "8:6",
    "6:8",
    "7:7",
}


def _expected_singles_count(team_result: str) -> int | None:
    match = re.fullmatch(r"(\d+)\s*:\s*(\d+)", team_result or "")
    if not match:
        return None
    home_wins, away_wins = int(match.group(1)), int(match.group(2))
    high, low = max(home_wins, away_wins), min(home_wins, away_wins)
    if high == 8 and low in (2, 3, 4, 5, 6):
        return low + 6
    if high in (9, 10) and low == 11 - high:
        return 8
    if (home_wins, away_wins) == (7, 7):
        return 12
    return None


def _game_winner_side(game: MatchGame) -> str | None:
    match = re.fullmatch(r"(\d+)\s*:\s*(\d+)", game.result or "")
    if not match:
        return None
    home, away = int(match.group(1)), int(match.group(2))
    if home == away:
        return None
    return "home" if home > away else "away"


def validate_database() -> dict:
    """Validate imported XTTV matches without modifying the database.

    Fewer than eight players are allowed for now and are deliberately not
    reported as an inconsistency. The fixed match math is nevertheless always
    validated: exactly two doubles and a valid number of singles for the final
    team result.
    """
    create_all()
    db = SessionLocal()
    try:
        matches = (
            db.query(XttvMatch)
            .options(selectinload(XttvMatch.players), selectinload(XttvMatch.games))
            .order_by(XttvMatch.id)
            .all()
        )

        issues: list[dict] = []
        player_count_under_8 = 0

        for match in matches:
            prefix = {"match_id": match.id, "meid": match.external_id}
            players = list(match.players)
            games = list(match.games)

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
                        issues.append({
                            **prefix,
                            "type": "player_positions",
                            "side": side,
                            "message": f"expected positions {sorted(expected)}, found {sorted(positions)}",
                        })
                    ids = [p.external_player_id for p in side_players]
                    if len(ids) != len(set(ids)):
                        issues.append({**prefix, "type": "duplicate_player_id", "side": side, "message": "duplicate external_player_id"})

            team_result = (match.team_result or "").replace(" ", "")
            if team_result not in _VALID_TEAM_RESULTS:
                issues.append({**prefix, "type": "team_result", "message": f"invalid team result {match.team_result!r}"})

            expected_singles = _expected_singles_count(team_result)
            singles = [g for g in games if g.game_type == "singles"]
            doubles = [g for g in games if g.game_type == "doubles"]

            if len(doubles) != 2:
                issues.append({**prefix, "type": "double_count", "message": f"expected 2 doubles, found {len(doubles)}"})
            if expected_singles is not None and len(singles) != expected_singles:
                issues.append({
                    **prefix,
                    "type": "single_count",
                    "message": f"result {team_result} requires {expected_singles} singles, found {len(singles)}",
                })

            sequences = [g.sequence for g in games]
            if len(sequences) != len(set(sequences)):
                issues.append({**prefix, "type": "duplicate_sequence", "message": f"duplicate game sequence numbers: {sequences}"})

            double_sequences = {g.sequence for g in doubles}
            if double_sequences != {5, 10}:
                issues.append({**prefix, "type": "double_sequences", "message": f"expected doubles at sequences 5 and 10, found {sorted(double_sequences)}"})

            for game in games:
                if _game_winner_side(game) is None:
                    issues.append({
                        **prefix,
                        "type": "game_result",
                        "sequence": game.sequence,
                        "message": f"invalid or tied game result {game.result!r}",
                    })

            parsed_home_wins = sum(_game_winner_side(g) == "home" for g in games)
            parsed_away_wins = sum(_game_winner_side(g) == "away" for g in games)
            if expected_singles is not None and f"{parsed_home_wins}:{parsed_away_wins}" != team_result:
                issues.append({
                    **prefix,
                    "type": "score_mismatch",
                    "message": f"team result is {team_result}, games add up to {parsed_home_wins}:{parsed_away_wins}",
                })

        return {
            "ok": len(issues) == 0,
            "matches_checked": len(matches),
            "matches_valid": len(matches) - len({i["match_id"] for i in issues}),
            "matches_with_issues": len({i["match_id"] for i in issues}),
            "player_count_under_8": player_count_under_8,
            "issues_count": len(issues),
            "issues": issues,
        }
    finally:
        db.close()


def get_match(meid: int):
    create_all()
    db = SessionLocal()
    try:
        match = db.query(XttvMatch).filter(XttvMatch.external_id == str(meid)).first()
        if not match:
            return {"ok": False, "error": "match_not_found", "meid": meid}

        players = (
            db.query(MatchPlayer)
            .filter(MatchPlayer.match_id == match.id)
            .order_by(MatchPlayer.side, MatchPlayer.position)
            .all()
        )
        games = (
            db.query(MatchGame)
            .filter(MatchGame.match_id == match.id)
            .order_by(MatchGame.sequence)
            .all()
        )

        return {
            "ok": True,
            "match": {
                "id": match.id,
                "meid": match.external_id,
                "league": match.league,
                "season": match.season,
                "match_date": match.match_date,
                "home_team": match.home_team,
                "away_team": match.away_team,
                "home_scheme": match.home_scheme,
                "away_scheme": match.away_scheme,
                "team_result": match.team_result,
            },
            "players": [
                {
                    "name": p.name,
                    "external_player_id": p.external_player_id,
                    "side": p.side,
                    "position": p.position,
                }
                for p in players
            ],
            "games": [
                {
                    "sequence": g.sequence,
                    "game_type": g.game_type,
                    "home_player": g.home_player,
                    "away_player": g.away_player,
                    "home_position": g.home_position,
                    "away_position": g.away_position,
                    "result": g.result,
                    "sets": g.sets,
                    "raw_row": g.raw_row,
                }
                for g in games
            ],
        }
    finally:
        db.close()
