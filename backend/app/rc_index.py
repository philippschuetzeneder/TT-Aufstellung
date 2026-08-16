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
USER_AGENT = "TT-Aufstellung/0.1 (+public RatingsCentral player index)"


def clean_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    return "".join(ch for ch in value if unicodedata.category(ch) not in {"Cf", "Cc"} or ch in "\t\n\r").strip()


def norm(value: str) -> str:
    value = clean_text(value).casefold().replace("ä", "a").replace("ö", "o").replace("ü", "u").replace("ß", "ss")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def parse_rc_players(html: str) -> list[dict]:
    """Parse RC PlayerSearch results.

    RC currently renders the search results as links/cards rather than a
    normal HTML table. Therefore we deliberately search the complete document
    for PlayerID references and derive the nearby player name from the link
    text or its containing element. The parser also retains the old row-based
    path as a fallback.
    """
    soup = BeautifulSoup(html, "html.parser")
    result: dict[int, dict] = {}

    def add(rc_id: int, name: str, cells: list[str] | None = None) -> None:
        name = clean_text(html_lib.unescape(name))
        if not name or len(name) > 160:
            return
        # RC names are normally "Surname, Firstname". Reject obvious UI text.
        if "," not in name:
            return
        if not re.search(r"[A-Za-zÀ-ÿ]", name):
            return
        result[rc_id] = {
            "rc_player_id": rc_id,
            "name": name,
            "name_norm": norm(name),
            "cells": cells or [name],
        }

    # Preferred path: every PlayerID link in the complete document.
    for link in soup.find_all("a", href=True):
        match = re.search(r"[?&]PlayerID=(\d+)", link.get("href", ""), re.I)
        if not match:
            continue
        rc_id = int(match.group(1))
        candidates: list[str] = []
        text = clean_text(" ".join(link.stripped_strings))
        if text:
            candidates.append(text)
        parent = link.parent
        if parent:
            candidates.append(clean_text(" ".join(parent.stripped_strings)))
        # Sometimes the name is in the closest card/container.
        for ancestor in list(link.parents)[:3]:
            value = clean_text(" ".join(ancestor.stripped_strings))
            if value and len(value) <= 300:
                candidates.append(value)
        name = next((c for c in candidates if "," in c and len(c) <= 160), None)
        if name:
            add(rc_id, name)

    # Fallback for table-based rendering.
    for row in soup.find_all("tr"):
        cells = [clean_text(" ".join(c.stripped_strings)) for c in row.find_all(["th", "td"])]
        if not cells:
            continue
        for link in row.find_all("a", href=True):
            match = re.search(r"[?&]PlayerID=(\d+)", link.get("href", ""), re.I)
            if not match:
                continue
            rc_id = int(match.group(1))
            name = next((c for c in cells if "," in c and len(c) <= 160), None)
            if name:
                add(rc_id, name, cells)

    # Last-resort regex: useful if RC embeds the result payload in script/data
    # attributes where BeautifulSoup does not expose a normal anchor.
    if not result:
        for match in re.finditer(r"PlayerID=(\d+)[^<]{0,500}", html, re.I):
            rc_id = int(match.group(1))
            fragment = BeautifulSoup(match.group(0), "html.parser").get_text(" ", strip=True)
            name_match = re.search(r"([A-ZÀ-ÖØ-Ý][^<>\n]{1,80},\s*[A-Za-zÀ-ÿ][^<>\n]{1,80})", fragment)
            if name_match:
                add(rc_id, name_match.group(1))

    return list(result.values())


def fetch_search(name_prefix: str) -> tuple[str, str]:
    params = {"Name": name_prefix, "PlayerSport": "Table Tennis", "Search": "Search"}
    url = f"{RC_BASE}/PlayerSearch.php?{urlencode(params)}"
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    with urlopen(req, timeout=20) as response:
        return response.read().decode("utf-8", errors="replace"), url


def debug_search(surname: str) -> dict:
    html, url = fetch_search(f"{surname.strip()},")
    soup = BeautifulSoup(html, "html.parser")
    tables = []
    for index, table in enumerate(soup.find_all("table")):
        rows = []
        for row in table.find_all("tr")[:20]:
            cells = [clean_text(" ".join(c.stripped_strings)) for c in row.find_all(["th", "td"])]
            if cells:
                rows.append(cells)
        tables.append({"index": index, "rows": rows})
    players = parse_rc_players(html)
    player_links = []
    for link in soup.find_all("a", href=True):
        match = re.search(r"[?&]PlayerID=(\d+)", link.get("href", ""), re.I)
        if match:
            player_links.append({"rc_player_id": int(match.group(1)), "text": clean_text(" ".join(link.stripped_strings)), "href": link.get("href")})
    return {
        "ok": True,
        "surname": surname,
        "url": url,
        "html_bytes": len(html.encode("utf-8")),
        "table_count": len(tables),
        "tables": tables,
        "player_link_count": len(player_links),
        "player_links": player_links[:50],
        "players": players[:50],
    }


def _surname(name: str) -> str:
    parts = [p for p in re.split(r"[\s,]+", clean_text(name)) if p]
    return parts[0] if parts else ""


def import_index(limit: int = 30, offset: int = 0, force: bool = False) -> dict:
    create_all()
    with SessionLocal() as session:
        players = session.query(XttvPlayer).filter(XttvPlayer.rc_player_id.is_(None)).order_by(XttvPlayer.id).offset(offset).limit(limit).all()
        surnames = sorted({_surname(p.name) for p in players if _surname(p.name)})

    results = []
    fetched = 0
    stored = 0
    for surname in surnames:
        key = f"surname:{norm(surname)}"
        with SessionLocal() as session:
            cached = session.query(RcPlayerIndex).filter_by(search_key=key).one_or_none()
            if cached and not force:
                results.append({"search_key": key, "surname": surname, "status": "cached", "players": cached.player_count})
                continue
        try:
            html, url = fetch_search(f"{surname},")
            players_found = parse_rc_players(html)
            fetched += 1
            with SessionLocal.begin() as session:
                entry = session.query(RcPlayerIndex).filter_by(search_key=key).one_or_none()
                if entry is None:
                    entry = RcPlayerIndex(search_key=key)
                    session.add(entry)
                entry.url = url
                entry.fetched_at = datetime.utcnow()
                entry.player_count = len(players_found)
                entry.players_json = players_found
                stored += len(players_found)
            results.append({"search_key": key, "surname": surname, "status": "fetched", "players": len(players_found)})
        except Exception as exc:
            results.append({"search_key": key, "surname": surname, "status": "error", "error": f"{type(exc).__name__}: {exc}"})

    return {"ok": True, "mode": "rc_index", "offset": offset, "limit": limit, "requested_players": len(players), "unique_surnames": len(surnames), "requests_made": fetched, "candidate_rows_stored": stored, "results": results}


def local_candidates(name: str, limit: int = 20) -> list[dict]:
    surname = _surname(name)
    key = f"surname:{norm(surname)}"
    with SessionLocal() as session:
        entry = session.query(RcPlayerIndex).filter_by(search_key=key).one_or_none()
        if entry is None:
            return []
        return list(entry.players_json or [])[:limit]
