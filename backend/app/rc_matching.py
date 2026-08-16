from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

from .db import SessionLocal, create_all
from .models import XttvPlayer
from .rc_index import local_candidates

RC_BASE = "https://www.ratingscentral.com"
USER_AGENT = "TT-Aufstellung/0.1 (+public RatingsCentral player lookup)"


def clean_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    return "".join(ch for ch in value if unicodedata.category(ch) not in {"Cf", "Cc"} or ch in "\t\n\r").strip()


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


def search_rc(name: str, limit: int = 20) -> list[dict]:
    # Prefer the persistent RC index. If it is not populated yet, use the
    # documented partial-name search as a fallback; importantly, RC expects
    # the surname prefix including the comma (e.g. "Wittinghofer,").
    indexed = local_candidates(name, limit=100)
    if indexed:
        candidates = []
        for c in indexed:
            score = score_name(name, c.get("name", ""))
            if score:
                candidates.append({**c, "score": score})
        return sorted(candidates, key=lambda c: (-c["score"], c["rc_player_id"]))[:limit]

    parts = [p for p in re.split(r"[\s,]+", clean_text(name)) if p]
    surname = parts[0] if parts else name
    params = {"Name": f"{surname},", "PlayerSport": "Table Tennis"}
    url = f"{RC_BASE}/PlayerSearch.php?{urlencode(params)}"
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    with urlopen(req, timeout=15) as response:
        html = response.read().decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")
    candidates = []
    for row in soup.find_all("tr"):
        cells = [clean_text(" ".join(c.stripped_strings)) for c in row.find_all(["th", "td"])]
        if not cells:
            continue
        rc_id = None
        for link in row.find_all("a", href=True):
            m = re.search(r"PlayerID=(\d+)", link["href"])
            if m:
                rc_id = int(m.group(1))
                break
        if rc_id is None:
            continue
        name_cell = next((c for c in cells if "," in c), None)
        if not name_cell:
            continue
        score = score_name(name, name_cell)
        if score:
            candidates.append({"rc_player_id": rc_id, "name": name_cell, "score": score, "cells": cells})
    unique = {}
    for c in candidates:
        if c["rc_player_id"] not in unique or c["score"] > unique[c["rc_player_id"]]["score"]:
            unique[c["rc_player_id"]] = c
    return sorted(unique.values(), key=lambda c: (-c["score"], c["rc_player_id"]))[:limit]


def dry_run(limit: int = 30, offset: int = 0) -> dict:
    create_all()
    with SessionLocal() as session:
        players = session.query(XttvPlayer).filter(XttvPlayer.rc_player_id.is_(None)).order_by(XttvPlayer.id).offset(offset).limit(limit).all()
        targets = [(p.external_player_id, p.name, p.club) for p in players]

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
            results.append({"xttv_player_id": external_id, "name": name, "club": club, "status": status, "candidate": candidate, "candidates": candidates[:10]})
        except Exception as exc:
            results.append({"xttv_player_id": external_id, "name": name, "club": club, "status": "error", "error": f"{type(exc).__name__}: {exc}"})

    return {
        "ok": True,
        "mode": "dry_run",
        "offset": offset,
        "limit": limit,
        "requested": len(targets),
        "matched": sum(r["status"] == "matched" for r in results),
        "ambiguous": sum(r["status"] == "ambiguous" for r in results),
        "not_found": sum(r["status"] == "not_found" for r in results),
        "errors": sum(r["status"] == "error" for r in results),
        "results": results,
    }
