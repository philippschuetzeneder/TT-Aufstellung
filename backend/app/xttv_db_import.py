"""XTTV Spielbericht import — incremental forward scan only."""

from __future__ import annotations

import re
import time
import urllib.error
from datetime import datetime

from bs4 import BeautifulSoup

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from .db import SessionLocal, create_all
from .models import MatchGame, MatchPlayer, RawSourceDocument, XttvMatch, XttvPlayer
from .xttv_import import fetch_match
from .xttv_parser import normalize_team_name, parse_match

TARGET_SEASONS = {"2025/2026", "2024/2025", "2023/2024"}
REFERENCE_MEID = 437757
MAX_IMPORT = 200
EMPTY_STREAK_STOP = 25
REPORT_NOT_YET_AVAILABLE = "Der Spielbericht von diesem Spiel ist noch nicht vorhanden."
REQUEST_DELAY = 0.05
# Wide forward horizon already verified once; persisted frontier skips re-scanning it.
SCAN_HORIZON_BOOTSTRAP_OFFSET = 50000


def _ensure_scan_meta_table() -> None:
    with SessionLocal.begin() as session:
        session.execute(text(
            "CREATE TABLE IF NOT EXISTS xttv_scan_meta (key text PRIMARY KEY, value text NOT NULL)"
        ))


def _get_scan_frontier() -> int | None:
    _ensure_scan_meta_table()
    with SessionLocal() as session:
        value = session.execute(
            text("SELECT value FROM xttv_scan_meta WHERE key = 'frontier_meid'"),
        ).scalar()
    return int(value) if value and str(value).isdigit() else None


def _set_scan_frontier(meid: int) -> None:
    _ensure_scan_meta_table()
    with SessionLocal.begin() as session:
        session.execute(
            text(
                "INSERT INTO xttv_scan_meta (key, value) VALUES ('frontier_meid', :value) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ),
            {"value": str(meid)},
        )


def _resolve_scan_frontier(last_known: int | None) -> int | None:
    frontier = _get_scan_frontier()
    if frontier is not None or last_known is None:
        return frontier
    seeded = last_known + SCAN_HORIZON_BOOTSTRAP_OFFSET
    _set_scan_frontier(seeded)
    return seeded


def _is_valid_4_player_report(parsed: dict) -> bool:
    return (
        not parsed.get("has_walkover")
        and parsed.get("player_count") == 8
        and parsed.get("singles_count") in (8, 9, 10, 11, 12)
        and parsed.get("doubles_count") == 2
    )


def _is_valid_walkover_report(parsed: dict) -> bool:
    return (
        parsed.get("has_walkover")
        and parsed.get("player_count") >= 4
        and parsed.get("singles_count") >= 4
    )


def _is_valid_importable_report(parsed: dict) -> bool:
    return _is_valid_4_player_report(parsed) or _is_valid_walkover_report(parsed)


def _upsert_player_master(session, external_player_id: str | None, name: str, club: str | None, observed_at: datetime) -> None:
    if not external_player_id:
        return
    external_player_id = str(external_player_id).strip()
    if not external_player_id:
        return
    player = session.query(XttvPlayer).filter_by(external_player_id=external_player_id).one_or_none()
    if player is None:
        player = XttvPlayer(
            external_player_id=external_player_id,
            name=name,
            club=club,
            source="xttv",
            first_seen_at=observed_at,
            last_seen_at=observed_at,
        )
        session.add(player)
    else:
        if name:
            player.name = name
        if club:
            player.club = club
        if player.first_seen_at is None or observed_at < player.first_seen_at:
            player.first_seen_at = observed_at
        if player.last_seen_at is None or observed_at > player.last_seen_at:
            player.last_seen_at = observed_at


def _resolve_match_player_id(player: dict) -> str | None:
    external_player_id = player.get("external_player_id")
    if external_player_id:
        return str(external_player_id).strip() or None
    side = player.get("side") or "x"
    position = player.get("position") or "x"
    return f"__nopass_{side}_{position}"


def _is_real_player_id(external_player_id: str | None) -> bool:
    return bool(external_player_id) and not str(external_player_id).startswith("__nopass_")


def _normalize_parsed_match(parsed: dict) -> dict:
    if parsed.get("home_team"):
        parsed["home_team"] = normalize_team_name(parsed["home_team"])
    if parsed.get("away_team"):
        parsed["away_team"] = normalize_team_name(parsed["away_team"])
    return parsed


def _persist_import(meid: int, html: str, status: int, content_type: str, url: str, parsed: dict) -> dict:
    parsed = _normalize_parsed_match(parsed)
    with SessionLocal.begin() as session:
        raw = session.query(RawSourceDocument).filter_by(source="xttv", external_id=str(meid)).one_or_none()
        if raw is None:
            raw = RawSourceDocument(source="xttv", external_id=str(meid), url=url, content=html)
            session.add(raw)
        else:
            raw.url, raw.content = url, html
        raw.http_status, raw.content_type, raw.fetched_at = status, content_type, datetime.utcnow()
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
        match.source_url, match.parsed_at = url, datetime.utcnow()
        match.players.clear()
        match.games.clear()
        session.flush()
        observed_at = datetime.utcnow()
        for player in parsed["players"]:
            external_player_id = _resolve_match_player_id(player)
            match.players.append(MatchPlayer(
                name=player["name"],
                external_player_id=external_player_id,
                side=player["side"],
                position=player.get("position"),
            ))
            if _is_real_player_id(external_player_id):
                _upsert_player_master(
                    session,
                    external_player_id,
                    player["name"],
                    parsed.get("home_team") if player.get("side") == "home" else parsed.get("away_team"),
                    observed_at,
                )
        for game in parsed["games"]:
            match.games.append(MatchGame(
                sequence=game.get("sequence"),
                game_type=game.get("game_type"),
                home_position=game.get("home_position"),
                away_position=game.get("away_position"),
                home_player=game.get("home_player"),
                away_player=game.get("away_player"),
                result=game.get("result"),
                sets=game.get("sets"),
                raw_row=game.get("raw_row"),
            ))
        session.flush()
        match_id = match.id
    return {
        "ok": True,
        "saved": True,
        "match_id": match_id,
        "meid": meid,
        "home_team": parsed["home_team"],
        "away_team": parsed["away_team"],
        "team_result": parsed["team_result"],
    }


def import_one(meid: int) -> dict:
    html, status, content_type, url = fetch_match(meid)
    parsed = parse_match(html, meid)
    if not _is_valid_importable_report(parsed):
        raise ValueError(
            f"Not a valid importable report: players={parsed['player_count']}, "
            f"singles={parsed['singles_count']}, doubles={parsed['doubles_count']}, "
            f"walkover={parsed.get('has_walkover')}"
        )
    try:
        return _persist_import(meid, html, status, content_type, url, parsed)
    except IntegrityError:
        return _persist_import(meid, html, status, content_type, url, parsed)


def _quick_report_info(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    text = " ".join(soup.stripped_strings)
    season_match = re.search(r"\b(20\d{2}/20\d{2})\b", text)
    season = season_match.group(1) if season_match else None
    three_player = bool(re.search(
        r"Heim-Mannschaft:\s*(?:A-C|1-3)|Gast-Mannschaft:\s*(?:A-C|1-3)", text, re.I,
    ))
    return {"season": season, "is_three_player": three_player}


def _report_not_yet_available(html: str) -> bool:
    return REPORT_NOT_YET_AVAILABLE in html


def _max_imported_meid() -> int | None:
    with SessionLocal() as session:
        rows = session.query(XttvMatch.external_id).all()
    ids = [int(row[0]) for row in rows if str(row[0]).isdigit()]
    return max(ids) if ids else None


def _is_imported(meid: int) -> bool:
    with SessionLocal() as session:
        return session.query(XttvMatch).filter_by(external_id=str(meid)).one_or_none() is not None


def _classify_meid(meid: int, *, check_db: bool = True) -> dict:
    """Classify one MEID without importing."""
    if check_db and _is_imported(meid):
        return {
            "meid": meid,
            "status": "already_imported",
            "miss": False,
            "importable": False,
        }
    try:
        html, _, _, _ = fetch_match(meid)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {"meid": meid, "status": "miss", "miss": True, "importable": False, "reason": "404"}
        return {
            "meid": meid,
            "status": "error",
            "miss": False,
            "importable": False,
            "reason": f"http_{exc.code}",
        }
    except Exception as exc:
        return {
            "meid": meid,
            "status": "error",
            "miss": False,
            "importable": False,
            "reason": type(exc).__name__,
        }

    if _report_not_yet_available(html):
        return {"meid": meid, "status": "miss", "miss": True, "importable": False, "reason": "not_yet"}

    quick = _quick_report_info(html)
    detail: dict = {
        "season": quick.get("season"),
        "three_player": quick.get("is_three_player"),
    }
    importable = False
    status = "valid_outside_filter"
    if quick["season"] not in TARGET_SEASONS:
        detail["filter_reason"] = "season"
    elif quick["is_three_player"]:
        detail["filter_reason"] = "three_player"
    else:
        try:
            parsed = parse_match(html, meid)
            if _is_valid_importable_report(parsed):
                importable = True
                status = "importable"
                detail["home_team"] = parsed.get("home_team")
                detail["away_team"] = parsed.get("away_team")
                detail["walkover"] = parsed.get("has_walkover")
            else:
                status = "invalid_report"
                detail["invalid_reason"] = (
                    f"players={parsed.get('player_count')}, singles={parsed.get('singles_count')}, "
                    f"doubles={parsed.get('doubles_count')}, walkover={parsed.get('has_walkover')}"
                )
        except Exception as exc:
            status = "parse_error"
            detail["parse_error"] = f"{type(exc).__name__}: {exc}"

    return {
        "meid": meid,
        "status": status,
        "miss": False,
        "importable": importable,
        **detail,
    }


def _try_import_classified(
    cls: dict,
    imported_ids: list[int],
    imported_details: list[dict],
    import_failures: list[dict],
    limit: int,
) -> bool:
    if len(imported_ids) >= limit or not cls.get("importable") or cls.get("status") == "already_imported":
        return False
    meid = int(cls["meid"])
    if _is_imported(meid):
        return False
    try:
        result = import_one(meid)
    except Exception as exc:
        failure = {
            "ok": False,
            "meid": meid,
            "error": f"{type(exc).__name__}: {exc}",
            "status": cls.get("status"),
        }
        import_failures.append(failure)
        imported_details.append(failure)
        return False
    imported_ids.append(meid)
    imported_details.append(result)
    return True


def _can_advance_frontier(cls: dict, import_succeeded: bool) -> bool:
    if cls.get("miss"):
        return True
    if cls.get("status") == "already_imported":
        return True
    if cls.get("status") == "valid_outside_filter":
        return True
    if cls.get("status") == "importable":
        return import_succeeded
    return False


def scan_import_reports(
    *,
    start: int,
    end: int,
    limit: int = MAX_IMPORT,
    direction: str = 'forward',
) -> dict:
    """Import reports in an explicit MEID range (used by xttv-auto-import.html)."""
    create_all()
    direction = str(direction or 'forward').lower()
    if direction not in {'forward', 'backward'}:
        raise ValueError('direction must be forward or backward')
    start = int(start)
    end = int(end)
    limit = min(max(int(limit), 1), MAX_IMPORT)
    if direction == 'forward' and start > end:
        raise ValueError('forward scan requires start <= end')
    if direction == 'backward' and start < end:
        raise ValueError('backward scan requires start >= end')

    step = 1 if direction == 'forward' else -1
    imported_ids: list[int] = []
    imported_details: list[dict] = []
    import_failures: list[dict] = []
    checked = errors = 0
    meid = start
    while (direction == 'forward' and meid <= end) or (direction == 'backward' and meid >= end):
        if len(imported_ids) >= limit:
            break
        if _is_imported(meid):
            meid += step
            continue
        checked += 1
        cls = _classify_meid(meid, check_db=False)
        import_succeeded = _try_import_classified(cls, imported_ids, imported_details, import_failures, limit)
        if cls.get('status') == 'error':
            errors += 1
            break
        if cls.get('status') in {'parse_error', 'invalid_report'}:
            errors += 1
        meid += step
        time.sleep(REQUEST_DELAY)

    return {
        'ok': True,
        'mode': 'range',
        'direction': direction,
        'range': {'start': start, 'end': end},
        'checked': checked,
        'imported': len(imported_ids),
        'imported_meids': imported_ids,
        'imported_details': imported_details,
        'import_failures': import_failures,
        'errors': errors,
    }


def import_new_reports(
    *,
    limit: int = MAX_IMPORT,
    empty_streak_stop: int = EMPTY_STREAK_STOP,
) -> dict:
    """Incremental forward scan from last frontier; stop after 25 consecutive misses.

    The scan frontier is persisted so future refreshes do not re-check old ID ranges.
    A miss is HTTP 404 or „Der Spielbericht von diesem Spiel ist noch nicht vorhanden.“
    Other valid pages outside the import filter do not count toward the miss streak.
    """
    create_all()
    limit = min(max(int(limit), 1), MAX_IMPORT)
    last_known = _max_imported_meid()
    scan_frontier = _resolve_scan_frontier(last_known)

    if last_known is not None:
        start = last_known + 1
        if scan_frontier is not None and scan_frontier >= start:
            start = scan_frontier + 1
    else:
        start = REFERENCE_MEID

    imported_ids: list[int] = []
    imported_details: list[dict] = []
    import_failures: list[dict] = []
    checked = errors = empty_streak = 0
    meid = start
    last_checked = start - 1
    frontier_candidate = start - 1

    while empty_streak < empty_streak_stop:
        if len(imported_ids) >= limit:
            break
        if _is_imported(meid):
            frontier_candidate = meid
            meid += 1
            continue
        checked += 1
        last_checked = meid
        cls = _classify_meid(meid, check_db=False)
        import_succeeded = False
        if cls.get("miss"):
            empty_streak += 1
            import_succeeded = True
        else:
            empty_streak = 0
            import_succeeded = _try_import_classified(
                cls, imported_ids, imported_details, import_failures, limit,
            )
        if cls.get("status") == "error":
            errors += 1
            break
        if cls.get("status") in {"parse_error", "invalid_report"}:
            errors += 1
        if _can_advance_frontier(cls, import_succeeded):
            frontier_candidate = meid
        elif cls.get("importable"):
            break
        meid += 1
        time.sleep(REQUEST_DELAY)

    if frontier_candidate >= start:
        _set_scan_frontier(frontier_candidate)

    return {
        "ok": True,
        "last_known_meid": last_known,
        "max_imported_meid": _max_imported_meid(),
        "scan_frontier_before": scan_frontier,
        "scan_frontier_after": frontier_candidate if frontier_candidate >= start else scan_frontier,
        "range": {"start": start, "end": last_checked if last_checked >= start else start - 1},
        "last_scanned_meid": last_checked if last_checked >= start else None,
        "checked": checked,
        "imported": len(imported_ids),
        "imported_meids": imported_ids,
        "imported_details": imported_details,
        "import_failures": import_failures,
        "errors": errors,
        "stopped_after_empty_streak": empty_streak >= empty_streak_stop,
    }


def player_ids_from_meids(meids: list[int]) -> list[str]:
    if not meids:
        return []
    with SessionLocal() as session:
        matches = session.query(XttvMatch).filter(
            XttvMatch.external_id.in_([str(m) for m in meids])
        ).all()
        ids: set[str] = set()
        for match in matches:
            for player in match.players:
                if player.external_player_id:
                    ids.add(str(player.external_player_id))
        return sorted(ids)


def player_master_status() -> dict:
    create_all()
    with SessionLocal() as session:
        master_ids = {
            str(value[0])
            for value in session.query(XttvPlayer.external_player_id)
            .filter(XttvPlayer.external_player_id.isnot(None))
            .all()
        }
        source_ids = {
            str(external_id)
            for external_id, _ in session.query(MatchPlayer.external_player_id, MatchPlayer.name)
            .filter(MatchPlayer.external_player_id.isnot(None))
            .all()
        }
        missing = sorted(source_ids - master_ids, key=lambda v: int(v) if v.isdigit() else v)
        return {
            "ok": True,
            "master_players": len(master_ids),
            "distinct_players_seen_in_matches": len(source_ids),
            "missing_from_master": len(missing),
        }


# Backward-compatible aliases for admin/debug endpoints
DEFAULT_LIMIT = MAX_IMPORT
MAX_IMPORT_LIMIT = MAX_IMPORT
scan_forward_for_new = import_new_reports

def scan_and_import(start: int, end: int, limit: int = DEFAULT_LIMIT, delay: float = REQUEST_DELAY) -> dict:
    """Admin-only: import a specific MEID range (not used by Daten Refresh)."""
    del delay
    return scan_import_reports(start=start, end=end, limit=min(limit, MAX_IMPORT))
