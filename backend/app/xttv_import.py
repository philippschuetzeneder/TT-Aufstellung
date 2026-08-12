from __future__ import annotations

import argparse
import re
import urllib.error
import urllib.request
from datetime import datetime
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup

from .db import SessionLocal, create_all
from .models import MatchGame, MatchPlayer, RawSourceDocument, XttvMatch

BASE = "https://oettv.xttv.at/ed/"
MATCH_URL = BASE + "spielbericht.inc.php?meid={meid}"


def fetch_match(meid: int) -> tuple[str, int, str, str]:
    """Fetch the actual XTTV match-report endpoint, not the JS/list page."""
    url = MATCH_URL.format(meid=meid)
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; TT-Aufstellung/0.1)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "de-AT,de;q=0.9,en;q=0.5",
            "Referer": f"{BASE}index.php?meid={meid}",
            "Connection": "keep-alive",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
        return body.decode(charset, errors="replace"), response.status, response.headers.get_content_type(), url


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def result_value(text: str) -> str | None:
    m = re.search(r"\b(\d{1,2})\s*[:\-]\s*(\d{1,2})\b", text)
    return f"{m.group(1)}:{m.group(2)}" if m else None


def position_value(text: str) -> str | None:
    text = clean(text).upper()
    m = re.search(r"\b([ABCD]|[1-4])\b", text)
    return m.group(1) if m else None


def player_id_from_link(link) -> str | None:
    href = link.get("href", "")
    params = parse_qs(urlparse(urljoin(BASE, href)).query)
    for key in ("spid", "playerid", "pid", "passnr", "passnummer"):
        if params.get(key):
            return params[key][0]
    return None


def parse_match(html: str, meid: int) -> dict:
    """First-pass parser for the XTTV Spielbericht HTML.

    We intentionally retain every table row as raw_row. Once the real 437757
    response is available to the runtime, the exact XTTV markup can be mapped
    without losing source information.
    """
    soup = BeautifulSoup(html, "html.parser")
    title = clean(soup.title.get_text(" ", strip=True)) if soup.title else None
    raw_text = clean(soup.get_text(" ", strip=True))
    text = raw_text

    # Common labels in XTTV reports. Keep extraction conservative; unknown
    # values remain NULL instead of being guessed.
    league = None
    season = None
    match_date = None
    for label, target in (("Liga", "league"), ("Saison", "season"), ("Datum", "match_date")):
        m = re.search(rf"{label}\s*[:\-]?\s*([^|]{{2,120}}?)(?=\s+(?:Liga|Saison|Datum|Runde|Heim|Gast)\b|$)", text, re.I)
        if m:
            locals()[target] = clean(m.group(1))

    # Extract all table rows. The final parser will classify rows using the
    # actual XTTV classes/attributes; these heuristics make the importer useful
    # against minor XTTV markup changes too.
    rows: list[list[str]] = []
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            cells = [clean(c.get_text(" ", strip=True)) for c in tr.find_all(["th", "td"])]
            cells = [c for c in cells if c]
            if cells:
                rows.append(cells)

    # Player links are retained separately. Position is assigned from nearby
    # text where available; never infer home/away from A-D vs 1-4.
    players: list[dict] = []
    seen: set[tuple[str, str | None, str | None]] = set()
    for link in soup.find_all("a", href=True):
        name = clean(link.get_text(" ", strip=True))
        pid = player_id_from_link(link)
        if not name or not pid or len(name) < 3:
            continue
        parent_text = clean(link.parent.get_text(" ", strip=True)) if link.parent else name
        pos = position_value(parent_text)
        side = None
        # Side is deliberately only assigned from explicit labels. We do not
        # use the A-D/1-4 scheme to decide home vs away.
        if re.search(r"\bheim\b", parent_text, re.I):
            side = "home"
        elif re.search(r"\bgast\b", parent_text, re.I):
            side = "away"
        key = (name, pid, pos)
        if key not in seen:
            seen.add(key)
            players.append({"name": name, "external_player_id": pid, "side": side, "position": pos})

    games: list[dict] = []
    sequence = 0
    for cells in rows:
        row_text = " | ".join(cells)
        result = result_value(row_text)
        if not result:
            continue
        # Avoid treating arbitrary metadata rows as games.
        if len(cells) < 3:
            continue
        sequence += 1
        lower = row_text.lower()
        game_type = "doubles" if "doppel" in lower else "singles" if "einzel" in lower else "unknown"
        games.append({
            "sequence": sequence,
            "game_type": game_type,
            "home_position": None,
            "away_position": None,
            "home_player": cells[0] if len(cells) >= 3 else None,
            "away_player": cells[1] if len(cells) >= 3 else None,
            "result": result,
            "sets": None,
            "raw_row": row_text,
        })

    return {
        "external_id": str(meid),
        "source_url": MATCH_URL.format(meid=meid),
        "title": title,
        "league": league,
        "season": season,
        "match_date": match_date,
        "home_team": None,
        "away_team": None,
        "home_scheme": None,
        "away_scheme": None,
        "team_result": None,
        "raw_text": raw_text,
        "players": players,
        "games": games,
    }


def save_match(meid: int, html: str, status: int, content_type: str, url: str, parsed: dict) -> None:
    with SessionLocal.begin() as session:
        raw = session.query(RawSourceDocument).filter_by(source="xttv", external_id=str(meid)).one_or_none()
        if raw is None:
            raw = RawSourceDocument(source="xttv", external_id=str(meid), url=url, content=html)
            session.add(raw)
        else:
            raw.url = url
            raw.content = html
        raw.http_status = status
        raw.content_type = content_type
        raw.fetched_at = datetime.utcnow()

        match = session.query(XttvMatch).filter_by(external_id=str(meid)).one_or_none()
        if match is None:
            match = XttvMatch(external_id=str(meid), source_url=url)
            session.add(match)
            session.flush()

        for field in ("title", "league", "season", "match_date", "home_team", "away_team", "home_scheme", "away_scheme", "team_result", "raw_text"):
            setattr(match, field, parsed[field])
        match.parsed_at = datetime.utcnow()

        # Re-import is idempotent. Raw source remains available for parser fixes.
        match.players.clear()
        match.games.clear()
        for player in parsed["players"]:
            match.players.append(MatchPlayer(**player))
        for game in parsed["games"]:
            match.games.append(MatchGame(**game))


def import_one(meid: int) -> None:
    create_all()
    html, status, content_type, url = fetch_match(meid)
    parsed = parse_match(html, meid)

    # Only OÖTTV four-player reports with doubles belong in the production
    # historical dataset. The raw response is still retained for diagnostics.
    text_lower = parsed["raw_text"].lower()
    is_ooe = any(x in text_lower for x in ("oö ttv", "oöettv", "oberösterreich", "ooettv"))
    has_doubles = any(g["game_type"] == "doubles" for g in parsed["games"])
    if not is_ooe:
        print(f"meid={meid}: fetched, but does not look like an OÖTTV report; raw response retained, not promoted.")
    elif not has_doubles:
        print(f"meid={meid}: fetched, but no doubles detected; raw response retained, not promoted.")
    else:
        save_match(meid, html, status, content_type, url, parsed)
        print(f"Imported XTTV meid={meid}: {len(parsed['players'])} players, {len(parsed['games'])} games")


def main() -> None:
    parser = argparse.ArgumentParser(description="Import one OÖTTV XTTV match into PostgreSQL")
    parser.add_argument("meid", type=int, help="XTTV match ID, e.g. 437757")
    args = parser.parse_args()
    try:
        import_one(args.meid)
    except urllib.error.URLError as exc:
        raise SystemExit(f"XTTV fetch failed for meid={args.meid}: {exc}") from exc


if __name__ == "__main__":
    main()
