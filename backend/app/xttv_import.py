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
    url = MATCH_URL.format(meid=meid)
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; TT-Aufstellung/0.1)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "de-AT,de;q=0.9,en;q=0.5",
            "Referer": f"{BASE}index.php",
            "Connection": "keep-alive",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read()
        charset = response.headers.get_content_charset() or "iso-8859-1"
        return body.decode(charset, errors="replace"), response.status, response.headers.get_content_type(), url


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def result_value(text: str) -> str | None:
    m = re.search(r"\b(\d{1,2})\s*[:\-]\s*(\d{1,2})\b", text)
    return f"{m.group(1)}:{m.group(2)}" if m else None


def player_id_from_link(link) -> str | None:
    href = link.get("href", "")
    params = parse_qs(urlparse(urljoin(BASE, href)).query)
    for key in ("spid", "playerid", "pid", "passnr", "passnummer"):
        if params.get(key):
            return params[key][0]
    return None


def inspect_html(html: str) -> dict:
    """Return structural information from a real XTTV response.

    This endpoint is intentionally diagnostic: it lets us see the actual
    XTTV markup from Render without guessing the HTML structure in advance.
    """
    soup = BeautifulSoup(html, "html.parser")
    tables = []
    for ti, table in enumerate(soup.find_all("table")):
        rows = []
        for tr in table.find_all("tr"):
            cells = [clean(c.get_text(" ", strip=True)) for c in tr.find_all(["th", "td"])]
            if cells:
                rows.append(cells)
        tables.append({"index": ti, "id": table.get("id"), "class": table.get("class"), "rows": rows})

    links = []
    for link in soup.find_all("a", href=True):
        text = clean(link.get_text(" ", strip=True))
        if text:
            links.append({"text": text, "href": link.get("href"), "player_id": player_id_from_link(link)})

    return {
        "title": clean(soup.title.get_text(" ", strip=True)) if soup.title else None,
        "meta_charset": next((m.get("content") for m in soup.find_all("meta") if "charset" in m.attrs), None),
        "table_count": len(tables),
        "tables": tables,
        "links": links,
        "text": clean(soup.get_text(" ", strip=True)),
    }


def _label_value(text: str, labels: tuple[str, ...]) -> str | None:
    pattern = r"(?:" + "|".join(re.escape(x) for x in labels) + r")\s*[:\-]?\s*(.+?)(?=\s+(?:Liga|Saison|Datum|Runde|Heim|Gast|Mannschaft|Ergebnis)\b|$)"
    m = re.search(pattern, text, re.I)
    return clean(m.group(1)) if m else None


def parse_match(html: str, meid: int) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    raw_text = clean(soup.get_text(" ", strip=True))
    title = clean(soup.title.get_text(" ", strip=True)) if soup.title else None

    league = _label_value(raw_text, ("Liga",))
    season = _label_value(raw_text, ("Saison",))
    match_date = _label_value(raw_text, ("Datum",))
    team_result = result_value(raw_text)

    # Do not infer home/away or A-D/1-4 from position letters. The actual
    # XTTV report must provide the team/position context in its tables.
    players: list[dict] = []
    seen: set[tuple[str, str | None, str | None, str | None]] = set()
    for link in soup.find_all("a", href=True):
        name = clean(link.get_text(" ", strip=True))
        pid = player_id_from_link(link)
        if not name or not pid or len(name) < 3:
            continue
        context = clean(link.parent.get_text(" ", strip=True)) if link.parent else name
        key = (name, pid, None, None)
        if key not in seen:
            seen.add(key)
            players.append({"name": name, "external_player_id": pid, "side": None, "position": None})

    games: list[dict] = []
    sequence = 0
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            cells = [clean(c.get_text(" ", strip=True)) for c in tr.find_all(["th", "td"])]
            cells = [c for c in cells if c]
            if len(cells) < 3:
                continue
            row_text = " | ".join(cells)
            result = result_value(row_text)
            if not result:
                continue
            sequence += 1
            lower = row_text.lower()
            game_type = "doubles" if "doppel" in lower else "singles" if "einzel" in lower else "unknown"
            games.append({
                "sequence": sequence,
                "game_type": game_type,
                "home_position": None,
                "away_position": None,
                "home_player": cells[0],
                "away_player": cells[1],
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
        "team_result": team_result,
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
        match.players.clear()
        match.games.clear()
        for player in parsed["players"]:
            match.players.append(MatchPlayer(**player))
        for game in parsed["games"]:
            match.games.append(MatchGame(**game))


def import_one(meid: int) -> dict:
    create_all()
    html, status, content_type, url = fetch_match(meid)
    parsed = parse_match(html, meid)
    text_lower = parsed["raw_text"].lower()
    is_ooe = any(x in text_lower for x in ("oö ttv", "oöettv", "oberösterreich", "ooettv"))
    has_doubles = any(g["game_type"] == "doubles" for g in parsed["games"])
    if is_ooe and has_doubles:
        save_match(meid, html, status, content_type, url, parsed)
    return {"parsed": parsed, "is_ooe": is_ooe, "has_doubles": has_doubles, "saved": is_ooe and has_doubles}


def main() -> None:
    parser = argparse.ArgumentParser(description="Import one OÖTTV XTTV match into PostgreSQL")
    parser.add_argument("meid", type=int)
    args = parser.parse_args()
    try:
        result = import_one(args.meid)
        print(result)
    except urllib.error.URLError as exc:
        raise SystemExit(f"XTTV fetch failed for meid={args.meid}: {exc}") from exc


if __name__ == "__main__":
    main()
