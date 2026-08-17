from __future__ import annotations

import re
import time
from datetime import datetime

from bs4 import BeautifulSoup

from .db import SessionLocal, create_all
from .models import MatchGame, MatchPlayer, RawSourceDocument, XttvMatch, XttvPlayer
from .xttv_import import fetch_match
from .xttv_parser import parse_match

TARGET_LEAGUE = "411 RK Linz Umg. / MV Mitte 2025/2026"
REFERENCE_MEID = 437757
DEFAULT_RADIUS = 150
DEFAULT_LIMIT = 20


def _is_valid_4_player_report(parsed: dict) -> bool:
    return parsed.get("player_count") == 8 and parsed.get("singles_count") in (8, 9, 10, 11, 12) and parsed.get("doubles_count") == 2


def _upsert_player_master(session, external_player_id: str | None, name: str, club: str | None, observed_at: datetime) -> None:
    if not external_player_id:
        return
    external_player_id = str(external_player_id).strip()
    if not external_player_id:
        return
    player = session.query(XttvPlayer).filter_by(external_player_id=external_player_id).one_or_none()
    if player is None:
        player = XttvPlayer(external_player_id=external_player_id, name=name, club=club, source="xttv", first_seen_at=observed_at, last_seen_at=observed_at)
        session.add(player)
    else:
        if name: player.name = name
        if club: player.club = club
        if player.first_seen_at is None or observed_at < player.first_seen_at: player.first_seen_at = observed_at
        if player.last_seen_at is None or observed_at > player.last_seen_at: player.last_seen_at = observed_at


def rebuild_player_master(limit: int = 5000, offset: int = 0) -> dict:
    """Build XTTV player master rows from already imported match players without refetching XTTV."""
    create_all()
    with SessionLocal.begin() as session:
        matches = session.query(XttvMatch).order_by(XttvMatch.id).offset(offset).limit(limit).all()
        seen = set()
        created = updated = skipped = 0
        for match in matches:
            observed_at = datetime.utcnow()
            for mp in match.players:
                key = mp.external_player_id
                if not key:
                    skipped += 1
                    continue
                before = session.query(XttvPlayer).filter_by(external_player_id=str(key)).one_or_none()
                _upsert_player_master(session, str(key), mp.name, match.home_team if mp.side == "home" else match.away_team, observed_at)
                seen.add(str(key))
                if before is None: created += 1
                else: updated += 1
    return {"ok": True, "matches_processed": len(matches), "unique_players_seen": len(seen), "created": created, "updated": updated, "skipped_without_external_id": skipped, "offset": offset, "limit": limit}


def player_master_status() -> dict:
    """Report player-master coverage against distinct XTTV player IDs already present in imported matches."""
    create_all()
    with SessionLocal() as session:
        master_ids = {str(value[0]) for value in session.query(XttvPlayer.external_player_id).filter(XttvPlayer.external_player_id.isnot(None)).all()}
        source_rows = session.query(MatchPlayer.external_player_id, MatchPlayer.name).filter(MatchPlayer.external_player_id.isnot(None)).all()
        source_ids = {str(external_id) for external_id, _ in source_rows}
        missing_ids = sorted(source_ids - master_ids, key=lambda value: int(value) if value.isdigit() else value)
        return {
            "ok": True,
            "master_players": len(master_ids),
            "distinct_players_seen_in_matches": len(source_ids),
            "missing_from_master": len(missing_ids),
            "coverage_percent": round((len(source_ids & master_ids) / len(source_ids)) * 100, 2) if source_ids else 100.0,
            "missing_external_player_ids": missing_ids[:100],
        }


def import_one(meid: int) -> dict:
    create_all()
    html, status, content_type, url = fetch_match(meid)
    parsed = parse_match(html, meid)
    if not _is_valid_4_player_report(parsed):
        raise ValueError(f"Not a valid 4-player report: players={parsed['player_count']}, singles={parsed['singles_count']}, doubles={parsed['doubles_count']}")
    with SessionLocal.begin() as session:
        raw = session.query(RawSourceDocument).filter_by(source="xttv", external_id=str(meid)).one_or_none()
        if raw is None: raw = RawSourceDocument(source="xttv", external_id=str(meid), url=url, content=html); session.add(raw)
        else: raw.url, raw.content = url, html
        raw.http_status, raw.content_type, raw.fetched_at = status, content_type, datetime.utcnow()
        match = session.query(XttvMatch).filter_by(external_id=str(meid)).one_or_none()
        if match is None: match = XttvMatch(external_id=str(meid), source_url=url); session.add(match); session.flush()
        for field in ("title", "league", "season", "match_date", "home_team", "away_team", "home_scheme", "away_scheme", "team_result", "raw_text"): setattr(match, field, parsed.get(field))
        match.source_url, match.parsed_at = url, datetime.utcnow(); match.players.clear(); match.games.clear(); session.flush()
        observed_at = datetime.utcnow()
        for player in parsed["players"]:
            match.players.append(MatchPlayer(name=player["name"], external_player_id=player.get("external_player_id"), side=player["side"], position=player.get("position")))
            _upsert_player_master(session, player.get("external_player_id"), player["name"], parsed.get("home_team") if player.get("side") == "home" else parsed.get("away_team"), observed_at)
        for game in parsed["games"]:
            match.games.append(MatchGame(sequence=game.get("sequence"), game_type=game.get("game_type"), home_position=game.get("home_position"), away_position=game.get("away_position"), home_player=game.get("home_player"), away_player=game.get("away_player"), result=game.get("result"), sets=game.get("sets"), raw_row=game.get("raw_row")))
        session.flush(); match_id = match.id
    return {"ok": True, "saved": True, "match_id": match_id, "meid": meid, "player_count": parsed["player_count"], "singles_count": parsed["singles_count"], "doubles_count": parsed["doubles_count"], "home_team": parsed["home_team"], "away_team": parsed["away_team"], "team_result": parsed["team_result"]}


def _already_imported(meid: int) -> bool:
    with SessionLocal() as session: return session.query(XttvMatch).filter_by(external_id=str(meid)).one_or_none() is not None


def _quick_report_info(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser"); tables = soup.find_all("table"); text = " ".join(soup.stripped_strings); league = None
    if len(tables) >= 2:
        rows = tables[1].find_all("tr")
        if rows:
            cells = rows[0].find_all(["th", "td"])
            if cells: league = " ".join(cells[0].stripped_strings)
    if not league:
        m = re.search(r"(\d+\s+[^\d\n]+?\s+20\d{2}/20\d{2})", text); league = m.group(1).strip() if m else None
    three_player = bool(re.search(r"Heim-Mannschaft:\s*(?:A-C|1-3)|Gast-Mannschaft:\s*(?:A-C|1-3)", text, re.I))
    return {"league": league, "is_three_player": three_player}


def _scan_order(start: int, end: int, reference: int) -> list[int]:
    if reference < start or reference > end: reference = start
    order = [reference]; distance = 1
    while reference - distance >= start or reference + distance <= end:
        if reference + distance <= end: order.append(reference + distance)
        if reference - distance >= start: order.append(reference - distance)
        distance += 1
    return order


def scan_and_import(start: int, end: int, limit: int = DEFAULT_LIMIT, delay: float = 0.05) -> dict:
    create_all()
    if end < start: raise ValueError("end must be >= start")
    if limit < 1 or limit > 100: raise ValueError("limit must be between 1 and 100")
    checked = candidates = imported = skipped_existing = errors = three_player = non_target = 0; hits=[]; error_samples=[]; reference=REFERENCE_MEID if start <= REFERENCE_MEID <= end else (start+end)//2
    for meid in _scan_order(start,end,reference):
        if imported >= limit: break
        checked += 1
        try:
            html,_,_,_=fetch_match(meid); quick=_quick_report_info(html)
            if quick["league"] != TARGET_LEAGUE: non_target += 1; time.sleep(delay); continue
            candidates += 1
            if quick["is_three_player"]: three_player += 1; hits.append({"meid":meid,"saved":False,"reason":"three_player_report"}); time.sleep(delay); continue
            parsed=parse_match(html,meid)
        except Exception as exc:
            errors += 1
            if len(error_samples)<20: error_samples.append({"meid":meid,"error":f"{type(exc).__name__}: {exc}"})
            time.sleep(delay); continue
        if not _is_valid_4_player_report(parsed):
            errors += 1
            if len(error_samples)<20: error_samples.append({"meid":meid,"error":f"invalid_4_player_report players={parsed.get('player_count')} singles={parsed.get('singles_count')} doubles={parsed.get('doubles_count')}"})
            continue
        if _already_imported(meid): skipped_existing += 1; hits.append({"meid":meid,"saved":False,"reason":"already_imported"}); continue
        try:
            result=import_one(meid); imported += 1; hits.append({"meid":meid,"saved":True,"match_id":result["match_id"],"home_team":result["home_team"],"away_team":result["away_team"],"team_result":result["team_result"]})
        except Exception as exc:
            errors += 1
            if len(error_samples)<20: error_samples.append({"meid":meid,"error":f"{type(exc).__name__}: {exc}"})
        time.sleep(delay)
    return {"ok":True,"target_league":TARGET_LEAGUE,"reference_meid":reference,"range":{"start":start,"end":end},"limit":limit,"checked":checked,"league_candidates":candidates,"imported":imported,"skipped_existing":skipped_existing,"three_player_skipped":three_player,"non_target":non_target,"errors":errors,"hits":hits,"error_samples":error_samples,"stopped_after_limit":imported>=limit}
