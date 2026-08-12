from __future__ import annotations

import time
from datetime import datetime

from .db import SessionLocal, create_all
from .models import MatchGame, MatchPlayer, RawSourceDocument, XttvMatch
from .xttv_import import fetch_match
from .xttv_parser import parse_match

TARGET_LEAGUE = "411 RK Linz Umg. / MV Mitte 2025/2026"
REFERENCE_MEID = 437757
DEFAULT_RADIUS = 1000
DEFAULT_LIMIT = 8


def import_one(meid: int) -> dict:
    create_all()
    html, status, content_type, url = fetch_match(meid)
    parsed = parse_match(html, meid)
    if parsed["player_count"] != 8 or parsed["singles_count"] != 12 or parsed["doubles_count"] != 2:
        raise ValueError(f"Not a complete 4-player report: players={parsed['player_count']}, singles={parsed['singles_count']}, doubles={parsed['doubles_count']}")

    with SessionLocal.begin() as session:
        raw = session.query(RawSourceDocument).filter_by(source="xttv", external_id=str(meid)).one_or_none()
        if raw is None:
            raw = RawSourceDocument(source="xttv", external_id=str(meid), url=url, content=html)
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
        for field in ("title", "league", "season", "match_date", "home_team", "away_team", "home_scheme", "away_scheme", "team_result", "raw_text"):
            setattr(match, field, parsed.get(field))
        match.source_url = url
        match.parsed_at = datetime.utcnow()
        match.players.clear()
        match.games.clear()
        session.flush()
        for player in parsed["players"]:
            match.players.append(MatchPlayer(name=player["name"], external_player_id=player.get("external_player_id"), side=player["side"], position=player.get("position")))
        for game in parsed["games"]:
            match.games.append(MatchGame(sequence=game.get("sequence"), game_type=game.get("game_type"), home_position=game.get("home_position"), away_position=game.get("away_position"), home_player=game.get("home_player"), away_player=game.get("away_player"), result=game.get("result"), sets=game.get("sets"), raw_row=game.get("raw_row")))
        session.flush()
        match_id = match.id

    return {"ok": True, "saved": True, "match_id": match_id, "meid": meid, "player_count": parsed["player_count"], "singles_count": parsed["singles_count"], "doubles_count": parsed["doubles_count"], "home_team": parsed["home_team"], "away_team": parsed["away_team"], "team_result": parsed["team_result"]}


def _already_imported(meid: int) -> bool:
    with SessionLocal() as session:
        return session.query(XttvMatch).filter_by(external_id=str(meid)).one_or_none() is not None


def scan_and_import(start: int, end: int, limit: int = DEFAULT_LIMIT, delay: float = 0.05) -> dict:
    """Scan a bounded MEID range and import only the exact target OÖTTV league."""
    create_all()
    if end < start:
        raise ValueError("end must be >= start")
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100")

    checked = candidates = imported = skipped_existing = errors = 0
    hits: list[dict] = []
    error_samples: list[dict] = []

    for meid in range(start, end + 1):
        if imported >= limit:
            break
        checked += 1
        try:
            html, _, _, _ = fetch_match(meid)
            parsed = parse_match(html, meid)
        except Exception as exc:
            errors += 1
            if len(error_samples) < 20:
                error_samples.append({"meid": meid, "error": f"{type(exc).__name__}: {exc}"})
            if delay:
                time.sleep(delay)
            continue

        if parsed.get("league") != TARGET_LEAGUE:
            if delay:
                time.sleep(delay)
            continue

        candidates += 1
        complete = parsed.get("player_count") == 8 and parsed.get("singles_count") == 12 and parsed.get("doubles_count") == 2
        if not complete:
            continue
        if _already_imported(meid):
            skipped_existing += 1
            hits.append({"meid": meid, "saved": False, "reason": "already_imported"})
            continue
        try:
            result = import_one(meid)
            imported += 1
            hits.append({"meid": meid, "saved": True, "match_id": result["match_id"], "home_team": result["home_team"], "away_team": result["away_team"], "team_result": result["team_result"]})
        except Exception as exc:
            errors += 1
            if len(error_samples) < 20:
                error_samples.append({"meid": meid, "error": f"{type(exc).__name__}: {exc}"})
        if delay:
            time.sleep(delay)

    return {"ok": True, "target_league": TARGET_LEAGUE, "range": {"start": start, "end": end}, "limit": limit, "checked": checked, "league_candidates": candidates, "imported": imported, "skipped_existing": skipped_existing, "errors": errors, "hits": hits, "error_samples": error_samples, "stopped_after_limit": imported >= limit}
