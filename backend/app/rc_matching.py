from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

from .db import SessionLocal, create_all
from .models import XttvPlayer
from .rc_index import import_index as rc_index_import
from .rc_index import local_candidates, to_rc_search_name

RC_BASE = "https://www.ratingscentral.com"
USER_AGENT = "TT-Aufstellung/0.1 (+public RatingsCentral player lookup)"


def clean_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    return "".join(
        ch for ch in value
        if unicodedata.category(ch) not in {"Cf", "Cc"} or ch in "\t\n\r"
    ).strip()


def norm_tokens(value: str) -> tuple[str, ...]:
    value = clean_text(value).casefold().replace(",", " ")
    value = value.replace("ä", "a").replace("ö", "o").replace("ü", "u").replace("ß", "ss")
    value = re.sub(r"[^\w\s-]", " ", value, flags=re.UNICODE)
    return tuple(sorted(t for t in value.split() if t))


def score_name(xttv_name: str, rc_name: str) -> int:
    a, b = norm_tokens(xttv_name), norm_tokens(rc_name)
    if not a or not b:
        return 0
    if a == b:
        return 100
    if len(a) == len(b) and set(a) == set(b):
        return 95
    return 0


def _parse_rc_search_results(html: str) -> list[dict]:
    """Parse the intermediate PlayerList.php result table."""
    soup = BeautifulSoup(html, "html.parser")
    unique: dict[int, dict] = {}
    for row in soup.find_all("tr"):
        cells = [clean_text(" ".join(c.stripped_strings)) for c in row.find_all(["th", "td"])]
        for link in row.find_all("a", href=True):
            match = re.search(r"[?&]PlayerID=(\d+)", link.get("href", ""), re.I)
            if not match:
                continue
            rc_id = int(match.group(1))
            name = clean_text(" ".join(link.stripped_strings))
            if "," not in name:
                name = next((cell for cell in cells if "," in cell), "")
            if not name or "," not in name:
                continue
            unique[rc_id] = {"rc_player_id": rc_id, "name": name, "cells": cells}
    return list(unique.values())


def search_rc(name: str, limit: int = 20) -> list[dict]:
    """Find an RC player using RC's real homepage search flow.

    First use the persistent cache. If this exact full-name search has not
    been cached yet, request PlayerList.php with 'Surname, Firstname'. The
    returned table contains the Player.php link and therefore the RC ID.
    """
    indexed = local_candidates(name, limit=100)
    if indexed:
        candidates = []
        for candidate in indexed:
            score = score_name(name, candidate.get("name", ""))
            if score:
                candidates.append({**candidate, "score": score})
        return sorted(candidates, key=lambda c: (-c["score"], c["rc_player_id"]))[:limit]

    search_name = to_rc_search_name(name)
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
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    with urlopen(request, timeout=20) as response:
        html = response.read().decode("utf-8", errors="replace")

    candidates = []
    for candidate in _parse_rc_search_results(html):
        score = score_name(name, candidate["name"])
        if score:
            candidates.append({**candidate, "score": score})
    return sorted(candidates, key=lambda c: (-c["score"], c["rc_player_id"]))[:limit]


def dry_run(limit: int = 30, offset: int = 0) -> dict:
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
        targets = [(p.external_player_id, p.name, p.club) for p in players]

    # Warm the persistent exact-name cache once for the whole batch. Duplicate
    # XTTV rows with the same person therefore share one RC request.
    prefetch = rc_index_import(limit=limit, offset=offset, force=False)

    results = []
    for external_id, name, club in targets:
        try:
            candidates = search_rc(name)
            exact = [c for c in candidates if c["score"] >= 95]
            if len(exact) == 1:
                status = "matched"
                candidate = exact[0]
            elif len(exact) > 1:
                status = "ambiguous"
                candidate = None
            else:
                status = "not_found"
                candidate = None
            results.append({
                "xttv_player_id": external_id,
                "name": name,
                "rc_search_name": to_rc_search_name(name),
                "club": club,
                "status": status,
                "candidate": candidate,
                "candidates": candidates[:10],
            })
        except Exception as exc:
            results.append({
                "xttv_player_id": external_id,
                "name": name,
                "rc_search_name": to_rc_search_name(name),
                "club": club,
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
            })

    return {
        "ok": True,
        "mode": "dry_run",
        "offset": offset,
        "limit": limit,
        "requested": len(targets),
        "prefetch": {
            "unique_search_names": prefetch.get("unique_search_names", 0),
            "requests_made": prefetch.get("requests_made", 0),
            "candidate_rows_stored": prefetch.get("candidate_rows_stored", 0),
        },
        "matched": sum(r["status"] == "matched" for r in results),
        "ambiguous": sum(r["status"] == "ambiguous" for r in results),
        "not_found": sum(r["status"] == "not_found" for r in results),
        "errors": sum(r["status"] == "error" for r in results),
        "results": results,
    }
