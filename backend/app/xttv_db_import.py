from __future__ import annotations

from datetime import datetime
import urllib.request

from .db import SessionLocal, create_all
from .models import MatchGame, MatchPlayer, RawSourceDocument, XttvMatch
from .xttv_import import MATCH_URL, fetch_match
from .xttv_parser import parse_match


def import_one(meid: int) -> dict:
    create_all()
    html, status, content_type, url = fetch_match(meid)
    parsed = parse_match(html, meid)

    if parsed["player_count"] != 8 or parsed["singles_count"] != 12 or parsed["doubles_count"] != 2:
        raise ValueError(
            f"Not a complete 4-player report: players={parsed['player_count']}, "
            f"singles={parsed['singles_count']}, doubles={parsed['doubles_count']}"
        )

    with SessionLocal.begin() as session:
        raw = session.query(RawSourceDocument).filter_by(
            source="xttv", external_id=str(meid)
        ).one_or_none()
        if raw is None:
            raw = RawSourceDocument(
                source="xttv", external_id=str(meid), url=url, content=html
            )
            session.add(raw)
        else:
            raw.url = url
            raw.content = html
        raw.http_status = status
        raw.content_type = content_type
        raw.fetched_at = datetime.utcnow()

        match = session.query(XttvMatch).filter_by(external_id=str(meid)).one_or_none()
        if match is None:
            match = XttvMatch(external_id=str(meid), source_url=url)
            session.add(match)
            session.flush()

        for field in (
            "title", "league", "season", "match_date", "home_team", "away_team",
            "home_scheme", "away_scheme", "team_result", "raw_text",
        ):
            setattr(match, field, parsed.get(field))
        match.source_url = url
        match.parsed_at = datetime.utcnow()

        match.players.clear()
        match.games.clear()
        session.flush()

        for player in parsed["players"]:
            match.players.append(
                MatchPlayer(
                    name=player["name"],
                    external_player_id=player.get("external_player_id"),
                    side=player["side"],
                    position=player.get("position"),
                )
            )

        for game in parsed["games"]:
            match.games.append(
                MatchGame(
                    sequence=game.get("sequence"),
                    game_type=game.get("game_type"),
                    home_position=game.get("home_position"),
                    away_position=game.get("away_position"),
                    home_player=game.get("home_player"),
                    away_player=game.get("away_player"),
                    result=game.get("result"),
                    sets=game.get("sets"),
                    raw_row=game.get("raw_row"),
                )
            )

        session.flush()
        match_id = match.id

    return {
        "ok": True,
        "saved": True,
        "match_id": match_id,
        "meid": meid,
        "player_count": parsed["player_count"],
        "singles_count": parsed["singles_count"],
        "doubles_count": parsed["doubles_count"],
        "home_team": parsed["home_team"],
        "away_team": parsed["away_team"],
        "team_result": parsed["team_result"],
    }
