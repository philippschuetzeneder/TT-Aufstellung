from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

from .db import SessionLocal, create_all
from .models import XttvPlayer
from .rc_index import import_index, local_candidates

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


def _surname(name: str) -> str:
    """Return the surname in either 'Surname, Firstname' or 'Firstname Surname'."""
    value = clean_text(name)
    if "," in value:
        return value.split(",", 1)[0].strip()
    parts = [p for p in re.split(r"\s+", value) if p]
    return parts[-1] if parts else value


def search_rc(name: str, limit: int = 20) -> list[dict]:
    # Prefer the persistent RC index. This means one RC PlayerSearch request
    # per surname, not one request per XTTV player and never a scan of events.
    indexed = local_candidates(name, limit=100)
    if indexed:
        candidates = []
        for c in indexed:
            score = score_name(name, c.get("name", ""))
            if score:
                candidates.append({**c, "score": score})
        return sorted(candidates, key=lambda c: (-c["score"], c["rc_player_id"]))[:limit]

    surname = _surname(name)
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


def match_players(limit: int = 100, offset: int = 0, force_index: bool = False) -> dict:
    """Find and persist safe XTTV -> RatingsCentral player mappings.

    The workflow is deliberately player-based: build/cache the RC surname index
    for the selected XTTV players, then persist only unique exact name matches.
    No EventSummary pages are queried.
    """
    create_all()
    with SessionLocal() as session:
        targets = (
            session.query(XttvPlayer)
            .filter(XttvPlayer.rc_player_id.is_(None))
            .order_by(XttvPlayer.id)
            .offset(offset)
            .limit(limit)
            .all()
        )
        target_ids = [p.id for p in targets]

    # Populate the persistent RC PlayerSearch cache first. Repeated surnames
    # result in one HTTP request only, and cached surnames are not re-fetched.
    index_result = import_index(limit=limit, offset=offset, force=force_index)
    dry = dry_run(limit=limit, offset=offset)

    applied = []
    skipped_conflict = []
    for result in dry["results"]:
        if result["status"] != "matched" or not result.get("candidate"):
            continue
        rc_id = int(result["candidate"]["rc_player_id"])
        xttv_id = result["xttv_player_id"]
        with SessionLocal.begin() as session:
            player = session.query(XttvPlayer).filter_by(external_player_id=xttv_id).one_or_none()
            owner = session.query(XttvPlayer).filter_by(rc_player_id=rc_id).one_or_none()
            if player is None:
                continue
            if owner is not None and owner.id != player.id:
                skipped_conflict.append({"xttv_player_id": xttv_id, "rc_player_id": rc_id, "reason": "rc_player_id_already_mapped", "existing_xttv_player_id": owner.external_player_id})
                continue
            player.rc_player_id = rc_id
            applied.append({"xttv_player_id": xttv_id, "name": player.name, "rc_player_id": rc_id, "rc_name": result["candidate"].get("name")})

    return {
        "ok": True,
        "mode": "player_based_match",
        "offset": offset,
        "limit": limit,
        "target_player_ids": target_ids,
        "index": index_result,
        "discovery": {k: dry[k] for k in ("requested", "matched", "ambiguous", "not_found", "errors")},
        "applied": len(applied),
        "conflicts": len(skipped_conflict),
        "mappings": applied,
        "conflict_details": skipped_conflict,
    }
