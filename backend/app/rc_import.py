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


def _parse_rating(value: str) -> tuple[float, float] | None:
    match = _RATING_RE.match(" ".join(value.split()))
    return (float(match.group(1)), float(match.group(2))) if match else None


def _clean_rc_name(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).replace("\u200b", "").replace("\ufeff", "").strip()
    value = re.sub(r"\s+\d+(?:\.\d+)?\s*(?:±|\+/-|\+-)\s*\d+(?:\.\d+)?\s*$", "", value)
    return value.strip(" ,")


def _normalized_name_tokens(value: str) -> tuple[str, ...]:
    value = unicodedata.normalize("NFKC", _clean_rc_name(value)).casefold().replace(",", " ")
    value = re.sub(r"[^\w\s-]", " ", value, flags=re.UNICODE)
    return tuple(sorted(token for token in value.split() if token))


def _find_xttv_player(session, rc_player_id: int, rc_name: str, xttv_player_id: str | None):
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
    candidates = session.query(XttvPlayer).all()
    matches = [p for p in candidates if _normalized_name_tokens(p.name) == rc_tokens]
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
        text = " ".join(soup.stripped_strings).replace("\u200b", "")
        match = re.search(r"(\d+)\s*(?:±|\+/-|\+-)\s*(\d+)", text)
        if not match:
            raise ValueError("Could not find current RC rating on PlayerHistory page")
        current_rating, current_deviation = float(match.group(1)), float(match.group(2))

    today = today or date.today()
    cutoff = cutoff or (today - timedelta(days=3 * 365 + 1))
    history: list[dict] = []
    for row in soup.find_all("tr"):
        cells = [" ".join(cell.stripped_strings) for cell in row.find_all(["th", "td"])]
        if len(cells) < 5:
            continue
        # RatingCentral uses YYYY-MM-DD in the history table.
        match = _DATE_RE.match(cells[0].strip())
        if not match:
            continue
        event_date = date.fromisoformat(cells[0].strip())
        if event_date < cutoff:
            continue
        initial = _parse_rating(cells[2])
        final = _parse_rating(cells[4])
        if initial is None or final is None:
            continue
        change_text = cells[3].replace("−", "-").replace("–", "-").strip()
        change_match = re.search(r"[+-]\s*\d+(?:\.\d+)?", change_text)
        history.append({
            "observed_at": event_date.isoformat(),
            "event": cells[1],
            "initial_rating": initial[0],
            "initial_deviation": initial[1],
            "point_change": float(change_match.group(0).replace(" ", "")) if change_match else None,
            "rc_rating": final[0],
            "rc_deviation": final[1],
        })

    # Some RC deployments render the table cells in a different order. As a
    # fallback, inspect every row for a date followed by two rating values.
    if not history:
        for row in soup.find_all("tr"):
            cells = [" ".join(cell.stripped_strings) for cell in row.find_all(["th", "td"])]
            if not cells:
                continue
            date_index = next((i for i, cell in enumerate(cells) if _DATE_RE.match(cell.strip())), None)
            if date_index is None:
                continue
            ratings = [(i, _parse_rating(cell)) for i, cell in enumerate(cells) if _parse_rating(cell) is not None]
            if len(ratings) < 2:
                continue
            event_date = date.fromisoformat(cells[date_index].strip())
            if event_date < cutoff:
                continue
            initial = ratings[0][1]
            final = ratings[-1][1]
            history.append({"observed_at": event_date.isoformat(), "event": cells[date_index + 1] if date_index + 1 < len(cells) else "", "initial_rating": initial[0], "initial_deviation": initial[1], "point_change": None, "rc_rating": final[0], "rc_deviation": final[1]})

    return {"name": name, "current": {"observed_at": today.isoformat(), "rc_rating": current_rating, "rc_deviation": current_deviation}, "history": history}


def import_rc_player(rc_player_id: int, *, xttv_player_id: str | None = None, xttv_external_player_id: str | None = None, xttv_name: str | None = None, xttv_club: str | None = None, cutoff: date | None = None) -> dict:
    create_all()
    html, content_type = fetch_player_history(rc_player_id)
    parsed = parse_player_history(html, cutoff=cutoff)
    source_external_id = f"playerhistory:{rc_player_id}"
    url = f"{RC_BASE}/PlayerHistory.php?PlayerID={rc_player_id}"
    with SessionLocal.begin() as session:
        raw = session.query(RawSourceDocument).filter_by(source="ratingscentral", external_id=source_external_id).one_or_none()
        if raw is None:
            raw = RawSourceDocument(source="ratingscentral", external_id=source_external_id, url=url, content=html)
            session.add(raw); session.flush()
        else:
            raw.url, raw.content, raw.fetched_at, raw.content_type = url, html, datetime.utcnow(), content_type
        raw.http_status = 200
        player = _find_xttv_player(session, rc_player_id, parsed["name"], xttv_player_id)
        if player is None and xttv_external_player_id:
            player = session.query(XttvPlayer).filter_by(external_player_id=xttv_external_player_id).one_or_none()
            if player is None:
                player = XttvPlayer(external_player_id=xttv_external_player_id, name=xttv_name or parsed["name"], club=xttv_club, source="xttv")
                session.add(player); session.flush()
        if player is None:
            raise ValueError(f"No XTTV player mapping found for RC {rc_player_id} ({parsed['name']}). Pass --xttv-player-id or provide --xttv-external-player-id.")
        if player.rc_player_id not in (None, rc_player_id):
            raise ValueError(f"XTTV player {player.external_player_id} is already mapped to RC {player.rc_player_id}")
        player.rc_player_id = rc_player_id
        if xttv_name: player.name = xttv_name
        if xttv_club: player.club = xttv_club
        observations = list(parsed["history"]) + [parsed["current"]]
        saved = 0
        for observation in observations:
            observed_at = datetime.fromisoformat(observation["observed_at"])
            snapshot = session.query(PlayerRatingSnapshot).filter_by(player_id=player.id, observed_at=observed_at, source="ratingscentral").one_or_none()
            if snapshot is None:
                snapshot = PlayerRatingSnapshot(player_id=player.id, observed_at=observed_at, source="ratingscentral"); session.add(snapshot)
            snapshot.rc_rating = observation["rc_rating"]; snapshot.rc_deviation = observation["rc_deviation"]; snapshot.source_document_id = raw.id; snapshot.imported_at = datetime.utcnow(); saved += 1
    return {"ok": True, "rc_player_id": rc_player_id, "name": parsed["name"], "xttv_player_id": player.external_player_id, "current": parsed["current"], "historical_observations": len(parsed["history"]), "snapshots_upserted": saved, "cutoff": (cutoff or (date.today() - timedelta(days=3 * 365 + 1))).isoformat()}


def main() -> None:
    parser = argparse.ArgumentParser(description="Import Ratings Central current rating and 3-year history")
    parser.add_argument("rc_player_id", type=int); parser.add_argument("--xttv-player-id"); parser.add_argument("--xttv-external-player-id"); parser.add_argument("--xttv-name"); parser.add_argument("--xttv-club"); parser.add_argument("--cutoff", type=date.fromisoformat)
    args = parser.parse_args()
    print(import_rc_player(args.rc_player_id, xttv_player_id=args.xttv_player_id, xttv_external_player_id=args.xttv_external_player_id, xttv_name=args.xttv_name, xttv_club=args.xttv_club, cutoff=args.cutoff))

if __name__ == "__main__": main()
