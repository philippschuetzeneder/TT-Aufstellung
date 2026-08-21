from __future__ import annotations

import argparse
import re
import unicodedata
from datetime import date, datetime, timedelta
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

from .db import SessionLocal, create_all
from .models import PlayerRatingSnapshot, RawSourceDocument, XttvPlayer

RC_BASE = "https://www.ratingscentral.com"
USER_AGENT = "TT-Aufstellung/0.1 (+public RatingsCentral history import)"
_RATING_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(?:±|\+/-|\+-)\s*(\d+(?:\.\d+)?)\s*$")
_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def fetch_player_history(rc_player_id: int) -> tuple[str, str]:
    url = f"{RC_BASE}/PlayerHistory.php?PlayerID={rc_player_id}"
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=15) as response:
        return response.read().decode("utf-8", errors="replace"), response.headers.get_content_type()


def _clean_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    return "".join(ch for ch in value if unicodedata.category(ch) not in {"Cf", "Cc"} or ch in "\t\n\r").strip()


def _parse_rating(value: str) -> tuple[float, float] | None:
    value = _clean_text(" ".join(value.split()))
    match = _RATING_RE.match(value)
    return (float(match.group(1)), float(match.group(2))) if match else None


def _clean_rc_name(value: str) -> str:
    value = _clean_text(value)
    value = re.sub(r"\s+\d+(?:\.\d+)?\s*(?:±|\+/-|\+-)\s*\d+(?:\.\d+)?\s*$", "", value)
    return value.strip(" ,")


def _normalized_name_tokens(value: str) -> tuple[str, ...]:
    value = unicodedata.normalize("NFKC", _clean_rc_name(value)).casefold().replace(",", " ")
    value = re.sub(r"[^\w\s-]", " ", value, flags=re.UNICODE)
    return tuple(sorted(token for token in value.split() if token))


def _find_xttv_player(session, rc_player_id: int, rc_name: str, xttv_player_id: str | None, xttv_external_player_id: str | None = None):
    # Explicit XTTV ID is the strongest mapping signal. This is important for
    # genuine duplicate-name cases such as the two Brandstetter Daniel records.
    if xttv_external_player_id:
        player = session.query(XttvPlayer).filter_by(external_player_id=xttv_external_player_id).one_or_none()
        if player is not None:
            return player
    player = session.query(XttvPlayer).filter_by(rc_player_id=rc_player_id).one_or_none()
    if player is not None:
        return player
    if xttv_player_id:
        return session.query(XttvPlayer).filter_by(external_player_id=xttv_player_id).one_or_none()
    rc_name = _clean_rc_name(rc_name)
    player = session.query(XttvPlayer).filter(XttvPlayer.name.ilike(rc_name)).one_or_none()
    if player is not None:
        return player
    rc_tokens = _normalized_name_tokens(rc_name)
    matches = [p for p in session.query(XttvPlayer).all() if _normalized_name_tokens(p.name) == rc_tokens]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError("Ambiguous XTTV player mapping for RC {} ({}): {}".format(rc_player_id, rc_name, ", ".join(f"{p.external_player_id}:{p.name}" for p in matches)))
    return None


def parse_player_history(html: str, *, cutoff: date | None = None, today: date | None = None) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.find("h1")
    raw_name = " ".join(heading.stripped_strings) if heading else None
    if not raw_name:
        raise ValueError("Could not find player name on PlayerHistory page")
    name = _clean_rc_name(raw_name)
    current_rating = current_deviation = None
    for node in soup.find_all(string=re.compile(r"±|\+/-|\+-")):
        parsed = _parse_rating(str(node))
        if parsed:
            current_rating, current_deviation = parsed
            break
    if current_rating is None:
        text = _clean_text(" ".join(soup.stripped_strings))
        match = re.search(r"(\d+)\s*(?:±|\+/-|\+-)\s*(\d+)", text)
        if not match:
            raise ValueError("Could not find current RC rating on PlayerHistory page")
        current_rating, current_deviation = float(match.group(1)), float(match.group(2))
    today = today or date.today()
    cutoff = cutoff or (today - timedelta(days=3 * 365 + 1))
    history: list[dict] = []
    for row in soup.find_all("tr"):
        cells = [_clean_text(" ".join(cell.stripped_strings)) for cell in row.find_all(["th", "td"])]
        if len(cells) < 5 or cells[0].strip() == "Date": continue
        if not _DATE_RE.match(cells[0].strip()): continue
        event_date = date.fromisoformat(cells[0].strip())
        if event_date < cutoff: continue
        initial, final = _parse_rating(cells[2]), _parse_rating(cells[4])
        if initial is None or final is None: continue
        change_text = _clean_text(cells[3]).replace("−", "-").replace("–", "-")
        change_match = re.search(r"[+-]\s*\d+(?:\.\d+)?", change_text)
        history.append({"observed_at": event_date.isoformat(), "event": cells[1], "initial_rating": initial[0], "initial_deviation": initial[1], "point_change": float(change_match.group(0).replace(" ", "")) if change_match else None, "rc_rating": final[0], "rc_deviation": final[1]})
    return {"name": name, "current": {"observed_at": today.isoformat(), "rc_rating": current_rating, "rc_deviation": current_deviation}, "history": history}


def import_rc_player(rc_player_id: int, *, xttv_player_id: str | None = None, xttv_external_player_id: str | None = None, xttv_name: str | None = None, xttv_club: str | None = None, cutoff: date | None = None) -> dict:
    create_all(); html, content_type = fetch_player_history(rc_player_id); parsed = parse_player_history(html, cutoff=cutoff)
    source_external_id = f"playerhistory:{rc_player_id}"; url = f"{RC_BASE}/PlayerHistory.php?PlayerID={rc_player_id}"
    with SessionLocal.begin() as session:
        raw = session.query(RawSourceDocument).filter_by(source="ratingscentral", external_id=source_external_id).one_or_none()
        if raw is None: raw = RawSourceDocument(source="ratingscentral", external_id=source_external_id, url=url, content=html); session.add(raw); session.flush()
        else: raw.url, raw.content, raw.fetched_at, raw.content_type = url, html, datetime.utcnow(), content_type
        raw.http_status = 200
        player = _find_xttv_player(session, rc_player_id, parsed["name"], xttv_player_id, xttv_external_player_id)
        if player is None and xttv_external_player_id:
            player = session.query(XttvPlayer).filter_by(external_player_id=xttv_external_player_id).one_or_none()
            if player is None:
                player = XttvPlayer(external_player_id=xttv_external_player_id, name=xttv_name or parsed["name"], club=xttv_club, source="xttv"); session.add(player); session.flush()
        if player is None: raise ValueError(f"No XTTV player mapping found for RC {rc_player_id} ({parsed['name']}).")
        if player.rc_player_id not in (None, rc_player_id): raise ValueError(f"XTTV player {player.external_player_id} is already mapped to RC {player.rc_player_id}")
        player.rc_player_id = rc_player_id
        if xttv_name: player.name = xttv_name
        if xttv_club: player.club = xttv_club
        saved = 0
        for observation in list(parsed["history"]) + [parsed["current"]]:
            observed_at = datetime.fromisoformat(observation["observed_at"])
            snapshot = session.query(PlayerRatingSnapshot).filter_by(player_id=player.id, observed_at=observed_at, source="ratingscentral").one_or_none()
            if snapshot is None: snapshot = PlayerRatingSnapshot(player_id=player.id, observed_at=observed_at, source="ratingscentral"); session.add(snapshot)
            snapshot.rc_rating = observation["rc_rating"]; snapshot.rc_deviation = observation["rc_deviation"]; snapshot.source_document_id = raw.id; snapshot.imported_at = datetime.utcnow(); saved += 1
    return {"ok": True, "rc_player_id": rc_player_id, "name": parsed["name"], "xttv_player_id": player.external_player_id, "current": parsed["current"], "historical_observations": len(parsed["history"]), "snapshots_upserted": saved}


def match_and_import_rc(limit: int = 30, offset: int = 0) -> dict:
    """Resolve eligible XTTV players and immediately persist RC identities/history."""
    from .rc_index import ensure_indexed_candidates
    from .rc_matching import _manual_override_candidate, resolve_candidates
    create_all()
    with SessionLocal() as session:
        players = (session.query(XttvPlayer).filter(XttvPlayer.rc_player_id.is_(None)).order_by(XttvPlayer.id).offset(offset).limit(limit).all())
        targets = [(p.external_player_id, p.name, p.club) for p in players]
    results = []
    for external_id, name, club in targets:
        try:
            override = _manual_override_candidate(external_id, name)
            if override is not None:
                rc_id = int(override["rc_player_id"]); reason = "manual override"
            else:
                candidates = ensure_indexed_candidates(name, allow_network=True, limit=100)
                status, candidate, ranked = resolve_candidates(name, candidates)
                if status != "matched" or candidate is None:
                    results.append({"xttv_player_id": external_id, "name": name, "status": status, "candidates": ranked[:10]}); continue
                rc_id = int(candidate["rc_player_id"]); reason = "matched"
            imported = import_rc_player(rc_id, xttv_external_player_id=external_id, xttv_name=name, xttv_club=club)
            results.append({"xttv_player_id": external_id, "name": name, "rc_player_id": rc_id, "status": "imported", "match_reason": reason, "historical_observations": imported["historical_observations"], "snapshots_upserted": imported["snapshots_upserted"]})
        except Exception as exc:
            results.append({"xttv_player_id": external_id, "name": name, "status": "error", "error": f"{type(exc).__name__}: {exc}"})
    return {"ok": True, "mode": "match_and_import", "offset": offset, "limit": limit, "requested": len(targets), "imported": sum(r["status"] == "imported" for r in results), "ambiguous": sum(r["status"] == "ambiguous" for r in results), "not_found": sum(r["status"] == "not_found" for r in results), "errors": sum(r["status"] == "error" for r in results), "results": results}


def bulk_import_rc(limit: int = 30, offset: int = 0) -> dict:
    # The bulk endpoint now performs matching + import for currently unmapped players.
    # Existing mapped players continue to be imported through the same endpoint.
    create_all()
    with SessionLocal() as session:
        unmapped = session.query(XttvPlayer).filter(XttvPlayer.rc_player_id.is_(None)).count()
    if unmapped:
        return match_and_import_rc(limit=limit, offset=offset)
    results=[]
    with SessionLocal() as session:
        players = session.query(XttvPlayer).filter(XttvPlayer.rc_player_id.isnot(None)).order_by(XttvPlayer.id).offset(offset).limit(limit).all()
        targets = [(p.id, p.external_player_id, p.rc_player_id, p.name, p.club) for p in players]
    for player_id, external_id, rc_id, name, club in targets:
        try:
            result = import_rc_player(int(rc_id), xttv_external_player_id=external_id, xttv_name=name, xttv_club=club)
            results.append({"xttv_player_id": external_id, "name": name, "status": "imported", "historical_observations": result["historical_observations"]})
        except Exception as exc:
            results.append({"xttv_player_id": external_id, "name": name, "status": "error", "error": f"{type(exc).__name__}: {exc}"})
    return {"ok": True, "offset": offset, "limit": limit, "requested": len(targets), "imported": sum(r["status"]=="imported" for r in results), "errors": sum(r["status"]=="error" for r in results), "results": results}


def main() -> None:
    parser = argparse.ArgumentParser(description="Import Ratings Central current rating and 3-year history")
    parser.add_argument("rc_player_id", type=int); parser.add_argument("--xttv-player-id"); parser.add_argument("--xttv-external-player-id"); parser.add_argument("--xttv-name"); parser.add_argument("--xttv-club"); parser.add_argument("--cutoff", type=date.fromisoformat)
    args = parser.parse_args(); print(import_rc_player(args.rc_player_id, xttv_player_id=args.xttv_player_id, xttv_external_player_id=args.xttv_external_player_id, xttv_name=args.xttv_name, xttv_club=args.xttv_club, cutoff=args.cutoff))

if __name__ == "__main__": main()
