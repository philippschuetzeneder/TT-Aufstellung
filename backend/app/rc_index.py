from __future__ import annotations

import html as html_lib
import re
import unicodedata
from datetime import datetime
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

from .db import SessionLocal, create_all
from .models import RcPlayerIndex, XttvPlayer

RC_BASE = "https://www.ratingscentral.com"
USER_AGENT = "TT-Aufstellung/0.1 (+public RatingsCentral player lookup)"


def clean_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    return "".join(
        ch for ch in value
        if unicodedata.category(ch) not in {"Cf", "Cc"} or ch in "\t\n\r"
    ).strip()


def norm(value: str) -> str:
    value = clean_text(value).casefold().translate(
        str.maketrans({"ä": "a", "ö": "o", "ü": "u", "ß": "ss"})
    )
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value).split())


def to_rc_search_name(name: str) -> str:
    """Convert XTTV's 'Surname Firstname' form to RC's 'Surname, Firstname'."""
    value = clean_text(name)
    if "," in value:
        surname, given = [part.strip() for part in value.split(",", 1)]
        return f"{surname}, {given}" if surname and given else value
    parts = [part for part in re.split(r"\s+", value) if part]
    if len(parts) >= 2:
        return f"{parts[0]}, {' '.join(parts[1:])}"
    return value


def _parse_rows(html: str) -> list[dict]:
    """Parse the intermediate RC PlayerList result page.

    PlayerList.php does not directly return the Player.php page. The result is
    a table containing a clickable player name whose href contains PlayerID.
    We deliberately take the anchor text as the canonical RC name and the
    PlayerID from that same row.
    """
    soup = BeautifulSoup(html, "html.parser")
    out: dict[int, dict] = {}

    def add(player_id: int | str | None, name: str, cells: list[str] | None = None) -> None:
        if not player_id:
            return
        name = clean_text(html_lib.unescape(name))
        if not name or "," not in name or len(name) > 160:
            return
        out[int(player_id)] = {
            "rc_player_id": int(player_id),
            "name": name,
            "name_norm": norm(name),
            "cells": cells or [name],
        }

    for row in soup.find_all("tr"):
        cells = [
            clean_text(" ".join(cell.stripped_strings))
            for cell in row.find_all(["th", "td"])
        ]
        if not cells:
            continue

        for link in row.find_all("a", href=True):
            match = re.search(r"[?&]PlayerID=(\d+)", link.get("href", ""), re.I)
            if not match:
                continue
            player_id = int(match.group(1))
            link_text = clean_text(" ".join(link.stripped_strings))
            name = link_text if "," in link_text else next(
                (
                    cell for cell in cells
                    if "," in cell and not re.fullmatch(r"\d+(?:\.\d+)?", cell)
                ),
                "",
            )
            add(player_id, name, cells)

    # Fallback for table markup where the PlayerID link is outside the row
    # structure that BeautifulSoup exposes normally.
    if not out:
        for link in soup.find_all("a", href=True):
            match = re.search(r"[?&]PlayerID=(\d+)", link.get("href", ""), re.I)
            if not match:
                continue
            name = clean_text(" ".join(link.stripped_strings))
            if "," in name:
                add(int(match.group(1)), name)

    return list(out.values())


def parse_rc_players(html: str) -> list[dict]:
    return _parse_rows(html)


def fetch_search(player_name: str) -> tuple[str, str]:
    """Run the actual Ratings Central homepage player search.

    RC expects the full name in 'Surname, Firstname' format and then returns
    an intermediate PlayerList.php table. The PlayerID in that table is the
    identifier used for the subsequent Player/PlayerHistory requests.
    """
    search_name = to_rc_search_name(player_name)
    params = {
        "PlayerName": search_name,
        "PlayerID": "",
        "PlayerUSATT_ID": "",
        "PlayerTTA_ID": "",
        "PlayerSport": "Any",
        "MinRating": "",
        "MaxRating": "",
        "MaxCurrentStDev": "",
        "MaxLastPlayedStDev": "",
        "MinLastPlayed": "",
        "MaxLastPlayed": "",
        "MinLastPlayedDate": "",
        "MaxLastPlayedDate": "",
    }
    url = f"{RC_BASE}/PlayerList.php?{urlencode(params)}"
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urlopen(request, timeout=20) as response:
        return response.read().decode("utf-8", errors="replace"), url


def debug_search(player_name: str) -> dict:
    html, url = fetch_search(player_name)
    soup = BeautifulSoup(html, "html.parser")
    players = parse_rc_players(html)
    return {
        "ok": True,
        "player_name": player_name,
        "search_name": to_rc_search_name(player_name),
        "url": url,
        "html_bytes": len(html.encode("utf-8")),
        "table_count": len(soup.find_all("table")),
        "player_link_count": len(
            [
                a for a in soup.find_all("a", href=True)
                if re.search(r"[?&]PlayerID=\d+", a.get("href", ""), re.I)
            ]
        ),
        "players": players[:50],
    }


def import_index(limit: int = 30, offset: int = 0, force: bool = False) -> dict:
    """Cache RC PlayerList results by unique full player name.

    This avoids one request for every XTTV row when several rows refer to the
    same player name, while keeping the exact full-name search required by RC.
    Cached searches are reused by the matching endpoint.
    """
    create_all()
    with SessionLocal() as session:
        players = (
            session.query(XttvPlayer)
            .filter(XttvPlayer.rc_player_id.is_(None))
            .order_by(XttvPlayer.id)
            .offset(offset)
            .limit(limit)
            .all()
        )
        search_names = sorted({to_rc_search_name(p.name) for p in players if p.name})

    results = []
    fetched = stored = 0
    for search_name in search_names:
        key = f"name:{norm(search_name)}"
        with SessionLocal() as session:
            cached = session.query(RcPlayerIndex).filter_by(search_key=key).one_or_none()
            if cached and not force:
                results.append(
                    {
                        "search_key": key,
                        "search_name": search_name,
                        "status": "cached",
                        "players": cached.player_count,
                    }
                )
                continue

        try:
            html, url = fetch_search(search_name)
            found = parse_rc_players(html)
            fetched += 1
            with SessionLocal.begin() as session:
                entry = session.query(RcPlayerIndex).filter_by(search_key=key).one_or_none()
                if entry is None:
                    entry = RcPlayerIndex(search_key=key)
                    session.add(entry)
                entry.url = url
                entry.fetched_at = datetime.utcnow()
                entry.player_count = len(found)
                entry.players_json = found
                stored += len(found)
            results.append(
                {
                    "search_key": key,
                    "search_name": search_name,
                    "status": "fetched",
                    "players": len(found),
                }
            )
        except Exception as exc:
            results.append(
                {
                    "search_key": key,
                    "search_name": search_name,
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    return {
        "ok": True,
        "mode": "rc_index",
        "offset": offset,
        "limit": limit,
        "requested_players": len(players),
        "unique_search_names": len(search_names),
        "requests_made": fetched,
        "candidate_rows_stored": stored,
        "results": results,
    }


def local_candidates(name: str, limit: int = 20) -> list[dict]:
    search_name = to_rc_search_name(name)
    key = f"name:{norm(search_name)}"
    with SessionLocal() as session:
        entry = session.query(RcPlayerIndex).filter_by(search_key=key).one_or_none()
        return list(entry.players_json or [])[:limit] if entry else []
