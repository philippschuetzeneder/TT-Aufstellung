from .db import SessionLocal, create_all
from .models import MatchGame, MatchPlayer, XttvMatch


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
