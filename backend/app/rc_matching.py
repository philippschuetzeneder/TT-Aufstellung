from __future__ import annotations

import re
import unicodedata
from datetime import date
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

from sqlalchemy import text

from .db import SessionLocal, create_all
from .models import XttvPlayer
from .rc_fallback import fallback_candidates
from .rc_index import ensure_indexed_candidates, import_index as rc_index_import
from .rc_index import local_candidates, to_rc_search_name
from .rc_manual_overrides import RC_PLAYER_OVERRIDES

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


def _candidate_last_played(candidate: dict) -> date | None:
    for cell in reversed(candidate.get("cells", [])):
        value = clean_text(str(cell))
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            try:
                return date.fromisoformat(value)
            except ValueError:
                pass
    return None


def _recency_score(candidate: dict, *, today: date | None = None) -> float:
    played = _candidate_last_played(candidate)
    if played is None:
        return 0.0
    today = today or date.today()
    days = max(0, (today - played).days)
    return max(0.0, 30.0 - days / 60.0)


def rank_candidates(xttv_name: str, candidates: list[dict], *, today: date | None = None) -> list[dict]:
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
    return sorted(ranked, key=lambda c: (-c["match_score"], -c["name_score"], c["rc_player_id"]))


def resolve_candidates(xttv_name: str, candidates: list[dict], *, today: date | None = None) -> tuple[str, dict | None, list[dict]]:
    ranked = rank_candidates(xttv_name, candidates, today=today)
    exact = [c for c in ranked if c["name_score"] >= 95]
    if not exact:
        return "not_found", None, ranked
    if len(exact) == 1:
        return "matched", exact[0], ranked
    top, second = exact[0], exact[1]
    if top["match_score"] - second["match_score"] >= 5.0:
        return "matched", top, ranked
    return "ambiguous", None, ranked


_RATING_CELL_RE = re.compile(r"(\d{3,4})\s*(?:±|\+/-|\+-)")


def _candidate_rc_rating(candidate: dict) -> int | None:
    for cell in candidate.get("cells", []):
        match = _RATING_CELL_RE.search(clean_text(str(cell)))
        if match:
            value = int(match.group(1))
            if 500 <= value <= 3000:
                return value
    return None


def _xttv_pseudo_rc_rating(wins: int, games: int) -> float:
    strength = (wins + 5.0) / (games + 10.0)
    return 1400.0 + (strength - 0.5) * 500.0


def _load_xttv_singles_summary(external_id: str) -> tuple[int, int]:
    with SessionLocal() as session:
        row = session.execute(text("""
            WITH games AS (
                SELECT CASE WHEN split_part(trim(g.result),':',1)::int > split_part(trim(g.result),':',2)::int THEN 1 ELSE 0 END AS win
                FROM match_games g
                JOIN match_players hp ON hp.match_id=g.match_id AND hp.side='home' AND hp.position=g.home_position
                WHERE g.game_type='singles' AND g.result ~ '^\\s*[0-9]+\\s*:\\s*[0-9]+\\s*$'
                  AND hp.external_player_id::text = :player_id
                UNION ALL
                SELECT CASE WHEN split_part(trim(g.result),':',2)::int > split_part(trim(g.result),':',1)::int THEN 1 ELSE 0 END
                FROM match_games g
                JOIN match_players ap ON ap.match_id=g.match_id AND ap.side='away' AND ap.position=g.away_position
                WHERE g.game_type='singles' AND g.result ~ '^\\s*[0-9]+\\s*:\\s*[0-9]+\\s*$'
                  AND ap.external_player_id::text = :player_id
            )
            SELECT coalesce(sum(win), 0) AS wins, count(*) AS games FROM games
        """), {"player_id": str(external_id)}).mappings().first()
    if not row:
        return 0, 0
    return int(row["wins"] or 0), int(row["games"] or 0)


def _disambiguate_duplicate_names(external_id: str, ranked: list[dict]) -> dict | None:
    exact = [c for c in ranked if c["name_score"] >= 95]
    if len(exact) < 2:
        return None

    wins, games = _load_xttv_singles_summary(external_id)
    if games >= 8:
        target = _xttv_pseudo_rc_rating(wins, games)
        rated = []
        for candidate in exact:
            rc_rating = _candidate_rc_rating(candidate)
            if rc_rating is None:
                continue
            rated.append((abs(rc_rating - target), candidate, rc_rating))
        if len(rated) >= 2:
            rated.sort(key=lambda item: item[0])
            if rated[0][0] + 40 <= rated[1][0]:
                return rated[0][1]
        elif len(rated) == 1:
            return rated[0][1]

    return None


def _parse_rc_search_results(html: str) -> list[dict]:
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
    candidates = []
    for candidate in ensure_indexed_candidates(name, allow_network=True, limit=100):
        score = score_name(name, candidate.get("name", ""))
        if score:
            candidates.append({**candidate, "score": score})
    return sorted(candidates, key=lambda c: (-c["score"], c["rc_player_id"]))[:limit]


def _manual_override_candidate(external_id: str, name: str) -> dict | None:
    rc_id = RC_PLAYER_OVERRIDES.get(str(external_id))
    if rc_id is None:
        return None
    return {"rc_player_id": rc_id, "name": name, "name_norm": " ".join(norm_tokens(name)), "score": 100, "name_score": 100, "recency_score": 0.0, "match_score": 100.0, "last_played": None, "manual_override": True}


def _resolve_player(
    external_id: str,
    name: str,
    club: str | None = None,
    *,
    allow_network_fallback: bool = False,
    allow_live_fetch: bool = True,
) -> dict:
    """Evaluate RC match status for one XTTV player (local index + optional live RC fetch)."""
    override = _manual_override_candidate(external_id, name)
    if override is not None:
        return {
            "xttv_player_id": external_id,
            "name": name,
            "club": club,
            "status": "matched",
            "candidate": override,
            "candidates": [override],
            "match_reason": "manual override",
        }
    candidates = ensure_indexed_candidates(name, allow_network=allow_live_fetch, limit=100)
    status, candidate, ranked = resolve_candidates(name, candidates)
    disambiguated = False
    if status == "ambiguous":
        candidate = _disambiguate_duplicate_names(external_id, ranked)
        if candidate is not None:
            status = "matched"
            disambiguated = True
    result = {
        "xttv_player_id": external_id,
        "name": name,
        "club": club,
        "rc_search_name": to_rc_search_name(name),
        "status": status,
        "candidate": candidate,
        "candidates": ranked[:10],
    }
    if candidate is not None:
        exact = [c for c in ranked if c["name_score"] >= 95]
        if disambiguated:
            result["match_reason"] = "exact name + XTTV singles strength matched to RC rating"
        elif len(exact) == 1:
            result["match_reason"] = "exact name + unique candidate"
        else:
            result["match_reason"] = "exact name + materially more recent RC activity"
    elif status == "ambiguous":
        result["match_reason"] = "multiple exact-name candidates with insufficient score separation"
    elif status == "not_found" and allow_network_fallback:
        ranked_fb = fallback_candidates(name, limit=10)
        if ranked_fb:
            top, second = ranked_fb[0], ranked_fb[1] if len(ranked_fb) > 1 else None
            if top["fallback_score"] >= 85.0 and (
                second is None or top["fallback_score"] - second["fallback_score"] >= 10.0
            ):
                candidate = {
                    **top,
                    "name_score": int(round(top.get("surname_similarity", 0) * 100)),
                    "recency_score": 0.0,
                    "match_score": top["fallback_score"],
                    "last_played": None,
                    "fuzzy_fallback": True,
                }
                result.update({
                    "status": "matched",
                    "candidate": candidate,
                    "candidates": ranked_fb[:10],
                    "match_reason": "conservative fuzzy RC fallback",
                })
            else:
                result["match_reason"] = "no exact-name RC candidate"
        else:
            result["match_reason"] = "no exact-name RC candidate"
    elif status == "not_found":
        result["match_reason"] = "no exact-name RC candidate"
    return result


def _rc_id_taken(session, rc_player_id: int, external_player_id: str) -> XttvPlayer | None:
    other = session.query(XttvPlayer).filter_by(rc_player_id=rc_player_id).one_or_none()
    if other is not None and str(other.external_player_id) != str(external_player_id):
        return other
    return None


def apply_matches(limit: int = 500, offset: int = 0, import_history: bool = True, only_unmapped: bool = True) -> dict:
    """Persist safe RC matches (matched + manual overrides). Skips ambiguous/not_found."""
    from .rc_import import import_rc_player

    create_all()
    with SessionLocal() as session:
        query = session.query(XttvPlayer).order_by(XttvPlayer.id)
        if only_unmapped:
            query = query.filter(XttvPlayer.rc_player_id.is_(None))
        players = query.offset(offset).limit(limit).all()
        targets = [(p.external_player_id, p.name, p.club) for p in players]

    applied = skipped = ambiguous = not_found = conflicts = errors = 0
    results: list[dict] = []
    for external_id, name, club in targets:
        try:
            resolved = _resolve_player(external_id, name, club, allow_network_fallback=False, allow_live_fetch=True)
            status = resolved["status"]
            if status == "ambiguous":
                ambiguous += 1
                results.append({**resolved, "action": "skipped"})
                continue
            if status == "not_found":
                not_found += 1
                results.append({**resolved, "action": "skipped"})
                continue
            if status != "matched" or resolved.get("candidate") is None:
                skipped += 1
                results.append({**resolved, "action": "skipped"})
                continue

            rc_id = int(resolved["candidate"]["rc_player_id"])
            with SessionLocal() as session:
                conflict = _rc_id_taken(session, rc_id, external_id)
                if conflict is not None:
                    conflicts += 1
                    results.append({
                        **resolved,
                        "action": "conflict",
                        "error": f"RC {rc_id} already mapped to {conflict.external_player_id} ({conflict.name})",
                    })
                    continue
                player = session.query(XttvPlayer).filter_by(external_player_id=str(external_id)).one_or_none()
                if player is None:
                    errors += 1
                    results.append({**resolved, "action": "error", "error": "XTTV player not found"})
                    continue
                if player.rc_player_id is not None and player.rc_player_id != rc_id:
                    conflicts += 1
                    results.append({
                        **resolved,
                        "action": "conflict",
                        "error": f"already mapped to RC {player.rc_player_id}",
                    })
                    continue
                player.rc_player_id = rc_id
                session.commit()

            entry = {
                **resolved,
                "rc_player_id": rc_id,
                "action": "mapped",
            }
            if import_history:
                imported = import_rc_player(
                    rc_id,
                    xttv_external_player_id=external_id,
                    xttv_name=name,
                    xttv_club=club,
                )
                entry["historical_observations"] = imported["historical_observations"]
                entry["snapshots_upserted"] = imported["snapshots_upserted"]
                entry["action"] = "imported"
            applied += 1
            results.append(entry)
        except Exception as exc:
            errors += 1
            results.append({
                "xttv_player_id": external_id,
                "name": name,
                "club": club,
                "status": "error",
                "action": "error",
                "error": f"{type(exc).__name__}: {exc}",
            })

    return {
        "ok": errors == 0,
        "mode": "apply_matches",
        "offset": offset,
        "limit": limit,
        "only_unmapped": only_unmapped,
        "import_history": import_history,
        "requested": len(targets),
        "applied": applied,
        "ambiguous": ambiguous,
        "not_found": not_found,
        "conflicts": conflicts,
        "skipped": skipped,
        "errors": errors,
        "results": results[:50],
    }


def apply_matches_all(batch_size: int = 200, import_history: bool = True, only_unmapped: bool = True) -> dict:
    """Apply matches for the full player master in resumable batches."""
    create_all()
    batch_size = min(max(int(batch_size), 1), 500)
    with SessionLocal() as session:
        query = session.query(XttvPlayer)
        if only_unmapped:
            query = query.filter(XttvPlayer.rc_player_id.is_(None))
        total = query.count()

    totals = {
        "requested": 0,
        "applied": 0,
        "ambiguous": 0,
        "not_found": 0,
        "conflicts": 0,
        "skipped": 0,
        "errors": 0,
    }
    batches = []
    while True:
        with SessionLocal() as session:
            query = session.query(XttvPlayer)
            if only_unmapped:
                query = query.filter(XttvPlayer.rc_player_id.is_(None))
            remaining = query.count()
        if remaining == 0:
            break
        result = apply_matches(
            limit=remaining,
            offset=0,
            import_history=import_history,
            only_unmapped=only_unmapped,
        )
        batch = {k: result[k] for k in totals}
        batches.append({"offset": 0, "limit": remaining, "remaining_before": remaining, **batch})
        for key in totals:
            totals[key] += batch[key]
        if batch["requested"] == 0 or batch["applied"] == 0:
            break

    with SessionLocal() as session:
        mapped = session.query(XttvPlayer).filter(XttvPlayer.rc_player_id.isnot(None)).count()

    return {
        "ok": totals["errors"] == 0,
        "mode": "apply_matches_all",
        "total_at_start": total,
        "batch_size": batch_size,
        "import_history": import_history,
        "only_unmapped": only_unmapped,
        "players_with_rc_id_after": mapped,
        "batches_processed": len(batches),
        **totals,
        "batches": batches,
    }


def dry_run(limit: int = 30, offset: int = 0) -> dict:
    create_all()
    with SessionLocal() as session:
        players = session.query(XttvPlayer).filter(XttvPlayer.rc_player_id.is_(None)).order_by(XttvPlayer.id).offset(offset).limit(limit).all()
        targets = [(p.external_player_id, p.name, p.club) for p in players]
    prefetch = rc_index_import(limit=limit, offset=offset, force=False)
    results = []
    for external_id, name, club in targets:
        try:
            override = _manual_override_candidate(external_id, name)
            if override is not None:
                results.append({"xttv_player_id": external_id, "name": name, "rc_search_name": to_rc_search_name(name), "club": club, "status": "matched", "candidate": override, "candidates": [override], "match_reason": "manual override"})
                continue
            candidates = search_rc(name)
            status, candidate, ranked = resolve_candidates(name, candidates)
            result = {"xttv_player_id": external_id, "name": name, "rc_search_name": to_rc_search_name(name), "club": club, "status": status, "candidate": candidate, "candidates": ranked[:10]}
            if candidate is not None:
                result["match_reason"] = "exact name + unique candidate" if len([c for c in ranked if c["name_score"] >= 95]) == 1 else "exact name + materially more recent RC activity"
            elif status == "ambiguous":
                result["match_reason"] = "multiple exact-name candidates with insufficient score separation"
            results.append(result)
        except Exception as exc:
            results.append({"xttv_player_id": external_id, "name": name, "rc_search_name": to_rc_search_name(name), "club": club, "status": "error", "error": f"{type(exc).__name__}: {exc}"})
    return {"ok": True, "mode": "dry_run", "offset": offset, "limit": limit, "requested": len(targets), "prefetch": {"unique_search_names": prefetch.get("unique_search_names", 0), "requests_made": prefetch.get("requests_made", 0), "candidate_rows_stored": prefetch.get("candidate_rows_stored", 0)}, "matched": sum(r["status"] == "matched" for r in results), "ambiguous": sum(r["status"] == "ambiguous" for r in results), "not_found": sum(r["status"] == "not_found" for r in results), "errors": sum(r["status"] == "error" for r in results), "results": results}


def dry_run_all() -> dict:
    create_all()
    with SessionLocal() as session:
        players = session.query(XttvPlayer).order_by(XttvPlayer.id).all()
        targets = [(p.external_player_id, p.name, p.club) for p in players]

    matched = ambiguous = not_found = errors = fallback_found = 0
    unresolved = []
    for external_id, name, club in targets:
        try:
            override = _manual_override_candidate(external_id, name)
            if override is not None:
                matched += 1
                continue
            candidates = ensure_indexed_candidates(name, allow_network=True, limit=100)
            status, candidate, ranked = resolve_candidates(name, candidates)
            if status == "matched":
                matched += 1
                continue
            if status == "ambiguous":
                ambiguous += 1
                unresolved.append({"xttv_player_id": external_id, "name": name, "club": club, "status": status, "candidates": ranked[:10], "match_reason": "multiple exact-name candidates with insufficient score separation"})
                continue
            fallback = fallback_candidates(name, limit=10)
            if fallback:
                fallback_found += 1
                unresolved.append({"xttv_player_id": external_id, "name": name, "club": club, "status": "not_found", "candidates": fallback, "match_reason": "no exact-name match; conservative fuzzy RC fallback candidates"})
            else:
                not_found += 1
                unresolved.append({"xttv_player_id": external_id, "name": name, "club": club, "status": "not_found", "candidates": [], "match_reason": "no exact-name RC candidate and no conservative fallback candidate"})
        except Exception as exc:
            errors += 1
            unresolved.append({"xttv_player_id": external_id, "name": name, "club": club, "status": "error", "candidates": [], "match_reason": f"{type(exc).__name__}: {exc}"})

    return {"ok": errors == 0, "mode": "match_dry_run_all", "total": len(targets), "matched": matched, "ambiguous": ambiguous, "not_found": not_found, "errors": errors, "fallback_candidates_found": fallback_found, "unresolved_count": len(unresolved), "unresolved": unresolved}
