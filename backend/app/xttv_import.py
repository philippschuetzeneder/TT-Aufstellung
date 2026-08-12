from __future__ import annotations

import argparse
import re
import urllib.request
from datetime import datetime
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup

from .db import SessionLocal, create_all
from .models import MatchGame, MatchPlayer, RawSourceDocument, XttvMatch

BASE_URL = "https://oettv.xttv.at/ed/index.php"


def fetch_match(meid: int) -> tuple[str, int, str]:
    url = f"{BASE_URL}?meid={meid}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "TT-Aufstellung/0.1 (+XTTV data import)",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "de-AT,de;q=0.9,en;q=0.5",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
        return body.decode(charset, errors="replace"), response.status, response.headers.get_content_type()


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def parse_match(html: str, meid: int) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    title = clean(soup.title.get_text(" ", strip=True)) if soup.title else None
    raw_text = clean(soup.get_text(" ", strip=True))

    players: list[dict] = []
    seen_players: set[tuple[str, str | None]] = set()
    for link in soup.find_all("a", href=True):
        text = clean(link.get_text(" ", strip=True))
        if not text or len(text) < 3:
            continue
        href = urljoin(BASE_URL, link["href"])
        params = parse_qs(urlparse(href).query)
        player_id = None
        for key in ("spid", "playerid", "pid", "passnr", "passnummer"):
            if params.get(key):
                player_id = params[key][0]
                break
        # Only retain likely player links. XTTV player links generally carry a
        # player identifier; names are kept exactly as shown by the source.
        if player_id:
            key = (text, player_id)
            if key not in seen_players:
                seen_players.add(key)
                players.append({"name": text, "external_player_id": player_id})

    games: list[dict] = []
    sequence = 0
    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = [clean(c.get_text(" ", strip=True)) for c in row.find_all(["th", "td"])]
            cells = [c for c in cells if c]
            if len(cells) < 2:
                continue
            row_text = " | ".join(cells)
            # A result-looking cell makes a table row a useful first-pass game
            # candidate. The raw row is deliberately retained until the exact
            # XTTV markup has been verified against a real response.
            if re.search(r"\b\d+\s*[:\-]\s*\d+\b", row_text):
                sequence += 1
                games.append({
                    "sequence": sequence,
                    "game_type": "unknown",
                    "home_player": cells[0] if len(cells) >= 3 else None,
                    "away_player": cells[1] if len(cells) >= 3 else None,
                    "result": next((c for c in cells if re.search(r"\d+\s*[:\-]\s*\d+", c)), None),
                    "raw_row": row_text,
                })

    return {
        "external_id": str(meid),
        "source_url": f"{BASE_URL}?meid={meid}",
        "title": title,
        "raw_text": raw_text,
        "players": players,
        "games": games,
    }


def save_match(meid: int, html: str, status: int, content_type: str, parsed: dict) -> None:
    with SessionLocal.begin() as session:
        raw = session.query(RawSourceDocument).filter_by(source="xttv", external_id=str(meid)).one_or_none()
        if raw is None:
            raw = RawSourceDocument(source="xttv", external_id=str(meid), url=parsed["source_url"], content=html)
            session.add(raw)
        else:
            raw.content = html
        raw.http_status = status
        raw.content_type = content_type
        raw.fetched_at = datetime.utcnow()

        match = session.query(XttvMatch).filter_by(external_id=str(meid)).one_or_none()
        if match is None:
            match = XttvMatch(external_id=str(meid), source_url=parsed["source_url"])
            session.add(match)
            session.flush()
        match.title = parsed["title"]
        match.raw_text = parsed["raw_text"]

        # Replace first-pass children on re-import so the importer is idempotent.
        match.players.clear()
        match.games.clear()
        for player in parsed["players"]:
            match.players.append(MatchPlayer(**player))
        for game in parsed["games"]:
            match.games.append(MatchGame(**game))


def import_one(meid: int) -> None:
    create_all()
    html, status, content_type = fetch_match(meid)
    parsed = parse_match(html, meid)
    save_match(meid, html, status, content_type, parsed)
    print(f"Imported XTTV meid={meid}")
    print(f"  title:   {parsed['title']!r}")
    print(f"  players: {len(parsed['players'])}")
    print(f"  games:   {len(parsed['games'])}")
    if parsed["games"]:
        for game in parsed["games"][:10]:
            print(f"    {game['raw_row']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Import one XTTV match into PostgreSQL")
    parser.add_argument("meid", type=int, help="XTTV match/event ID, e.g. 437757")
    args = parser.parse_args()
    import_one(args.meid)


if __name__ == "__main__":
    main()
