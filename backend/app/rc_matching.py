from __future__ import annotations

import re
import unicodedata
from datetime import date
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

from .db import SessionLocal, create_all
from .models import XttvPlayer
from .rc_index import import_index as rc_index_import
from .rc_index import local_candidates, to_rc_search_name
from .rc_manual_overrides import RC_PLAYER_OVERRIDES

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


def _candidate_last_played(candidate: dict) -> date | None:
    """Extract RC's last-played date from the PlayerList row."""
    for cell in reversed(candidate.get("cells", [])):
        value = clean_text(str(cell))
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            try:
                return date.fromisoformat(value)
            except ValueError:
                pass
    return None


def _recency_score(candidate: dict, *, today: date | None = None) -> float:
    """Give modest extra weight to a recently active RC identity.

    Name equality remains the primary signal. Recency is only a tie-breaker
    between otherwise exact-name candidates. The score decays over roughly
    five years and is capped at 30 points so it can never outweigh a name
    mismatch.
    """
    played = _candidate_last_played(candidate)
    if played is None:
        return 0.0
    today = today or date.today()
    days = max(0, (today - played).days)
    return max(0.0, 30.0 - days / 60.0)


def rank_candidates(xttv_name: str, candidates: list[dict], *, today: date | None = None) -> list[dict]:
    """Rank exact-name RC candidates using name score plus activity recency."""
    ranked = []
    for candidate in candidates:
        name_score = score_name(xttv_name, candidate.get("name", ""))
        recency = _recency_score(candidate, today=today)
        last_played = _candidate_last_played(candidate)
        ranked.append({
            **candidate,
            "name_score": name_score,
            "recency_score": round(recency, 2),
            "match_score": round(name_score + recency, 2),
            "last_played": last_played.isoformat() if last_played else None,
        })
    return sorted(
        ranked,
        key=lambda c: (-c["match_score"], -c["name_score"], c["rc_player_id"]),
    )


def resolve_candidates(xttv_name: str, candidates: list[dict], *, today: date | None = None) -> tuple[str, dict | None, list[dict]]:
    """Resolve a name search without making unsafe guesses.

    An exact-name candidate can be auto-selected only when it is uniquely
    best and beats the second exact-name candidate by at least 10 points.
    This resolves stale-vs-current duplicate RC identities while leaving
    genuinely indistinguishable duplicates ambiguous.
    """
    ranked = rank_candidates(xttv_name, candidates, today=today)
    exact = [c for c in ranked if c["name_score"] >= 95]
    if not exact:
        return "not_found", None, ranked
    if len(exact) == 1:
        return "matched", exact[0], ranked

    top, second = exact[0], exact[1]
    margin = top["match_score"] - second["match_score"]
    if margin >= 10.0:
        return "matched", top, ranked
    return "ambiguous", None, ranked


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


def _manual_override_candidate(external_id: str, name: str) -> dict | None:
    rc_id = RC_PLAYER_OVERRIDES.get(str(external_id))
    if rc_id is None:
        return None
    return {
        "rc_player_id": rc_id,
        "name": name,
        "name_norm": " ".join(norm_tokens(name)),
        "score": 100,
        "name_score": 100,
        "recency_score": 0.0,
        "match_score": 100.0,
        "last_played": None,
        "manual_override": True,
    }


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

    prefetch = rc_index_import(limit=limit, offset=offset, force=False)

    results = []
    for external_id, name, club in targets:
        try:
            override = _manual_override_candidate(external_id, name)
            if override is not None:
                result = {
                    "xttv_player_id": external_id,
                    "name": name,
                    "rc_search_name": to_rc_search_name(name),
                    "club": club,
                    "status": "matched",
                    "candidate": override,
                    "candidates": [override],
                    "match_reason": "manual override",
                }
                results.append(result)
                continue

            candidates = search_rc(name)
            status, candidate, ranked = resolve_candidates(name, candidates)
            result = {
                "xttv_player_id": external_id,
                "name": name,
                "rc_search_name": to_rc_search_name(name),
                "club": club,
                "status": status,
                "candidate": candidate,
                "candidates": ranked[:10],
            }
            if candidate is not None:
                result["match_reason"] = (
                    "exact name + unique candidate"
                    if len([c for c in ranked if c["name_score"] >= 95]) == 1
                    else "exact name + materially more recent RC activity"
                )
            elif status == "ambiguous":
                result["match_reason"] = "multiple exact-name candidates with insufficient score separation"
            results.append(result)
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


def dry_run_all() -> dict:
    """Run the RC matcher once across the complete XTTV master.

    This endpoint is intentionally read-only. It uses only the already-built
    local RC index and never falls back to live RatingsCentral requests. The
    response contains aggregate counts plus only unresolved/error cases, so a
    full 3,832-player run remains practical to inspect from the API.
    """
    create_all()
    with SessionLocal() as session:
        players = (
            session.query(XttvPlayer)
            .filter(XttvPlayer.rc_player_id.is_(None))
            .order_by(XttvPlayer.id)
            .all()
        )
        targets = [(p.external_player_id, p.name, p.club) for p in players]

    matched = ambiguous = not_found = errors = 0
    unresolved = []

    for external_id, name, club in targets:
        try:
            override = _manual_override_candidate(external_id, name)
            if override is not None:
                matched += 1
                continue

            # Full-run deliberately uses only the completed local RC index.
            # Do not trigger thousands of live RC requests if an index entry
            # is missing; such a player is reported as not_found instead.
            candidates = local_candidates(name, limit=100)
            status, candidate, ranked = resolve_candidates(name, candidates)
            if status == "matched":
                matched += 1
            elif status == "ambiguous":
                ambiguous += 1
                unresolved.append({
                    "xttv_player_id": external_id,
                    "name": name,
                    "club": club,
                    "status": status,
                    "candidates": ranked[:10],
                    "match_reason": "multiple exact-name candidates with insufficient score separation",
                })
            else:
                not_found += 1
                unresolved.append({
                    "xttv_player_id": external_id,
                    "name": name,
                    "club": club,
                    "status": "not_found",
                    "candidates": ranked[:10],
                })
        except Exception as exc:
            errors += 1
            unresolved.append({
                "xttv_player_id": external_id,
                "name": name,
                "club": club,
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
            })

    total = len(targets)
    return {
        "ok": errors == 0,
        "mode": "match_dry_run_all",
        "total": total,
        "matched": matched,
        "ambiguous": ambiguous,
        "not_found": not_found,
        "errors": errors,
        "unresolved": unresolved,
    }
