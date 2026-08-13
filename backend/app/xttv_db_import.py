from __future__ import annotations

import re
import time
from datetime import datetime

from bs4 import BeautifulSoup

from .db import SessionLocal, create_all
from .models import MatchGame, MatchPlayer, RawSourceDocument, XttvMatch
from .xttv_import import fetch_match
from .xttv_parser import parse_match

TARGET_LEAGUE = "411 RK Linz Umg. / MV Mitte 2025/2026"
REFERENCE_MEID = 437757
DEFAULT_RADIUS = 150
DEFAULT_LIMIT = 20


def _is_valid_4_player_report(parsed: dict) -> bool:
    """A valid 4-player report has both doubles and 8-12 singles.

    XTTV can end the singles portion early once the team result is decided,
    so 8, 9, 10, 11 and 12 singles are all valid formats for this league.
    """
    return (
        parsed.get("player_count") == 8
        and parsed.get("singles_count") in (8, 9, 10, 11, 12)
        and parsed.get("doubles_count") == 2
    )


def import_one(meid: int) -> dict:
    create_all()
    html, status, content_type, url = fetch_match(meid)
    parsed = parse_match(html, meid)
    if not _is_valid_4_player_report(parsed):
        raise ValueError(
            f"Not a valid 4-player report: players={parsed['player_count']}, "
            f"singles={parsed['singles_count']}, doubles={parsed['doubles_count']}"
        )

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


def _quick_report_info(html: str) -> dict:
    """Extract enough metadata to classify a page before the strict 4-player parser."""
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    text = " ".join(soup.stripped_strings)
    league = None
    if len(tables) >= 2:
        rows = tables[1].find_all("tr")
        if rows:
            cells = rows[0].find_all(["th", "td"])
            if cells:
                league = " ".join(cells[0].stripped_strings)
    if not league:
        m = re.search(r"(\d+\s+[^\d\n]+?\s+20\d{2}/20\d{2})", text)
        league = m.group(1).strip() if m else None
    three_player = bool(re.search(r"Heim-Mannschaft:\s*(?:A-C|1-3)|Gast-Mannschaft:\s*(?:A-C|1-3)", text, re.I))
    return {"league": league, "is_three_player": three_player}


def _scan_order(start: int, end: int, reference: int) -> list[int]:
    """Search outward from the known good MEID instead of scanning the whole range linearly."""
    if reference < start or reference > end:
        reference = start
    order = [reference]
    distance = 1
    while reference - distance >= start or reference + distance <= end:
        if reference + distance <= end:
            order.append(reference + distance)
        if reference - distance >= start:
            order.append(reference - distance)
        distance += 1
    return order


def scan_and_import(start: int, end: int, limit: int = DEFAULT_LIMIT, delay: float = 0.05) -> dict:
    """Targeted XTTV scan: exact league, complete 4-player reports, outward from reference MEID."""
    create_all()
    if end < start:
        raise ValueError("end must be >= start")
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100")

    checked = candidates = imported = skipped_existing = errors = three_player = non_target = 0
    hits: list[dict] = []
    error_samples: list[dict] = []
    reference = REFERENCE_MEID if start <= REFERENCE_MEID <= end else (start + end) // 2

    for meid in _scan_order(start, end, reference):
        if imported >= limit:
            break
        checked += 1
        try:
            html, _, _, _ = fetch_match(meid)
            quick = _quick_report_info(html)
            if quick["league"] != TARGET_LEAGUE:
                non_target += 1
                if delay:
                    time.sleep(delay)
                continue
            candidates += 1
            if quick["is_three_player"]:
                three_player += 1
                hits.append({"meid": meid, "saved": False, "reason": "three_player_report"})
                if delay:
                    time.sleep(delay)
                continue
            parsed = parse_match(html, meid)
        except Exception as exc:
            errors += 1
            if len(error_samples) < 20:
                error_samples.append({"meid": meid, "error": f"{type(exc).__name__}: {exc}"})
            if delay:
                time.sleep(delay)
            continue

        if not _is_valid_4_player_report(parsed):
            errors += 1
            if len(error_samples) < 20:
                error_samples.append({
                    "meid": meid,
                    "error": (
                        f"invalid_4_player_report players={parsed.get('player_count')} "
                        f"singles={parsed.get('singles_count')} doubles={parsed.get('doubles_count')}"
                    ),
                })
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

    return {
        "ok": True,
        "target_league": TARGET_LEAGUE,
        "reference_meid": reference,
        "range": {"start": start, "end": end},
        "limit": limit,
        "checked": checked,
        "league_candidates": candidates,
        "imported": imported,
        "skipped_existing": skipped_existing,
        "three_player_skipped": three_player,
        "non_target": non_target,
        "errors": errors,
        "hits": hits,
        "error_samples": error_samples,
        "stopped_after_limit": imported >= limit,
    }
