from __future__ import annotations

import re
from difflib import SequenceMatcher

from .rc_index import fetch_search, parse_rc_players


def _parts(name: str) -> tuple[str, str]:
    value = re.sub(r"\s+", " ", (name or "").strip())
    if "," in value:
        surname, given = [p.strip() for p in value.split(",", 1)]
    else:
        bits = value.split()
        surname, given = (bits[-1], " ".join(bits[:-1])) if len(bits) >= 2 else (value, "")
    return surname.casefold(), given.casefold()


def _compact(value: str) -> str:
    return re.sub(r"[^a-z0-9äöüß]+", "", value.casefold())


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _compact(a), _compact(b)).ratio()


def _score(xttv_name: str, rc_name: str) -> tuple[float, float, float]:
    xs, xg = _parts(xttv_name)
    rs, rg = _parts(rc_name)
    surname = _similarity(xs, rs)
    given = _similarity(xg, rg)
    return surname * 70.0 + given * 30.0, surname, given


def fallback_candidates(name: str, limit: int = 20) -> list[dict]:
    """Search RC with surname and given-name separately and rank fuzzy hits."""
    surname, given = _parts(name)
    queries = [q for q in (surname, given.split()[0] if given else "") if q]
    found: dict[int, dict] = {}
    for query in queries:
        try:
            html, _ = fetch_search(query)
            for candidate in parse_rc_players(html):
                found[candidate["rc_player_id"]] = candidate
        except Exception:
            continue
    ranked = []
    for candidate in found.values():
        total, surname_score, given_score = _score(name, candidate.get("name", ""))
        if surname_score >= 0.72 and given_score >= 0.55:
            ranked.append({**candidate, "fallback_score": round(total, 2), "surname_similarity": round(surname_score, 3), "given_name_similarity": round(given_score, 3)})
    return sorted(ranked, key=lambda c: (-c["fallback_score"], c["rc_player_id"]))[:limit]
