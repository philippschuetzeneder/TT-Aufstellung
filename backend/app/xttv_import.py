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


def player_id_from_text(text: str) -> str | None:
    m = re.search(r"PassNr\s+(\d+)", text, re.I)
    return m.group(1) if m else None


def result_value(text: str) -> str | None:
    m = re.search(r"\b(\d{1,2})\s*:\s*(\d{1,2})\b", text)
    return f"{m.group(1)}:{m.group(2)}" if m else None


def player_id_from_link(link) -> str | None:
    href = link.get("href", "")
    params = parse_qs(urlparse(urljoin(BASE, href)).query)
    for key in ("spid", "playerid", "pid", "passnr", "passnummer"):
        if params.get(key):
            return params[key][0]
    return None


def inspect_html(html: str) -> dict:
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


def _extract_team(cell: str, prefix: str) -> tuple[str | None, str | None]:
    m = re.search(re.escape(prefix) + r"\s*(.+?)\s*\(([^)]+)\)", cell)
    if not m:
        return None, None
    return clean(m.group(1)), clean(m.group(2))


def _extract_position_players(cell: str) -> tuple[str, str, str | None]:
    """Extract position, player name and PassNr from e.g. 'A: PassNr 17245 Rauch Gerhard'."""
    m = re.match(r"\s*([A-D1-4])\s*:\s*PassNr\s+(\d+)\s+(.+?)\s*$", cell)
    if not m:
        raise ValueError(f"Unexpected XTTV lineup cell: {cell!r}")
    return m.group(1), clean(m.group(3)), m.group(2)


def _extract_doubles(cell: str) -> list[dict]:
    pairs = []
    pattern = re.compile(
        r"Doppel\s*\((\d+)\):\s*(\d+)\s*/\s*(\d+)\s+(.+?)\s+Doppel\s*\((\d+)\):\s*(\d+)\s*/\s*(\d+)\s+(.+?)(?=\s*$)",
        re.I,
    )
    m = pattern.search(cell)
    if m:
        pairs.append({"sequence": int(m.group(1)), "pass_numbers": [m.group(2), m.group(3)], "players": clean(m.group(4))})
        pairs.append({"sequence": int(m.group(5)), "pass_numbers": [m.group(6), m.group(7)], "players": clean(m.group(8))})
    return pairs


def parse_match(html: str, meid: int) -> dict:
    """Parse the current OÖTTV 4-player XTTV match-report layout.

    The report explicitly tells us which side uses A-D and which uses 1-4.
    We preserve that fact instead of deriving home/away from the position label.
    """
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    if len(tables) < 2:
        raise ValueError("XTTV report does not contain the expected two tables")

    rows = []
    for tr in tables[0].find_all("tr"):
        cells = [clean(c.get_text(" ", strip=True)) for c in tr.find_all(["th", "td"])]
        if cells:
            rows.append(cells)
    if len(rows) < 2 or len(rows[0]) < 5 or len(rows[1]) < 5:
        raise ValueError("XTTV header layout is not recognized")

    home_cell = rows[0][1]
    home_team_cell = rows[0][2]
    team_result = result_value(rows[0][4])
    round_date = rows[1][0]
    away_cell = rows[1][1]
    away_team_cell = rows[1][2]

    home_is_letters = "A-D" in home_cell
    away_is_numbers = "1-4" in away_cell
    if home_is_letters == away_is_numbers:
        raise ValueError("Could not determine the A-D / 1-4 assignment from XTTV header")

    home_team, home_code = _extract_team(home_team_cell, "")
    away_team, away_code = _extract_team(away_team_cell, "")
    # _extract_team with an empty prefix is intentionally anchored to the first name/code pair.
    if not home_team or not away_team:
        m = re.match(r"(.+?)\s*\(([^)]+)\)", home_team_cell)
        if m:
            home_team, home_code = clean(m.group(1)), clean(m.group(2))
        m = re.match(r"(.+?)\s*\(([^)]+)\)", away_team_cell)
        if m:
            away_team, away_code = clean(m.group(1)), clean(m.group(2))

    date_match = re.search(r"Datum:\s*(\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2})", round_date)
    round_match = re.search(r"Runde:\s*(\d+)\s*\(Durchgang\s*(\d+)\)", round_date)

    competition = clean("".join([clean(c.get_text(" ", strip=True)) for c in tables[1].find_all("tr")[0].find_all(["th", "td"])[0:1]]))
    table1_rows = []
    for tr in tables[1].find_all("tr"):
        cells = [clean(c.get_text(" ", strip=True)) for c in tr.find_all(["th", "td"])]
        if cells:
            table1_rows.append(cells)
    if table1_rows and table1_rows[0]:
        competition = table1_rows[0][0]

    players: list[dict] = []
    player_by_position: dict[tuple[str, str], dict] = {}
    pass_to_player: dict[str, dict] = {}
    games: list[dict] = []
    doubles_info: dict[int, dict] = {}

    # Row 0 contains the home A-D lineup and both home doubles pairings.
    header = table1_rows[0]
    for cell in header:
        if re.match(r"^[A-D]:\s*PassNr", cell):
            pos, name, pid = _extract_position_players(cell)
            p = {"name": name, "external_player_id": pid, "side": "home", "position": pos}
            players.append(p)
            player_by_position[("home", pos)] = p
            pass_to_player[pid] = p
        elif "Doppel" in cell:
            for pair in _extract_doubles(cell):
                doubles_info[pair["sequence"]] = {"home": pair}

    # Rows 1-4 contain away lineup in col 0 and one result per populated A-D cell.
    for row in table1_rows[1:5]:
        if not row:
            continue
        pos, name, pid = _extract_position_players(row[0])
        p = {"name": name, "external_player_id": pid, "side": "away", "position": pos}
        players.append(p)
        player_by_position[("away", pos)] = p
        pass_to_player[pid] = p
        for col_index, cell in enumerate(row[1:5]):
            if not cell:
                continue
            for match in re.finditer(r"\((\d+)\)\s+(\d+):(\d+)\s+([A-Za-z0-9]+)", cell):
                sequence = int(match.group(1))
                result = f"{match.group(2)}:{match.group(3)}"
                winner_code = match.group(4)
                home_pos = chr(ord("A") + col_index)
                winner_side = "home" if winner_code == home_code else "away" if winner_code == away_code else None
                games.append({
                    "sequence": sequence,
                    "game_type": "singles",
                    "home_position": home_pos,
                    "away_position": pos,
                    "home_player": player_by_position[("home", home_pos)]["name"],
                    "away_player": p["name"],
                    "result": result,
                    "sets": result,
                    "winner_side": winner_side,
                    "raw_row": cell,
                })

    # Final row contains both doubles pairs and their two results.
    if len(table1_rows) >= 6:
        final = table1_rows[5]
        if final:
            for cell in final:
                if "Doppel" in cell:
                    for pair in _extract_doubles(cell):
                        doubles_info.setdefault(pair["sequence"], {})["away"] = pair
                for match in re.finditer(r"\((5|10)\)\s+(\d+):(\d+)\s+([A-Za-z0-9]+)", cell):
                    seq = int(match.group(1))
                    result = f"{match.group(2)}:{match.group(3)}"
                    winner_code = match.group(4)
                    pair = doubles_info.get(seq, {})
                    home_pair = pair.get("home", {}).get("players")
                    away_pair = pair.get("away", {}).get("players")
                    winner_side = "home" if winner_code == home_code else "away" if winner_code == away_code else None
                    games.append({
                        "sequence": seq,
                        "game_type": "doubles",
                        "home_position": None,
                        "away_position": None,
                        "home_player": home_pair,
                        "away_player": away_pair,
                        "result": result,
                        "sets": result,
                        "winner_side": winner_side,
                        "raw_row": cell,
                    })

    games.sort(key=lambda g: g["sequence"])
    raw_text = clean(soup.get_text(" ", strip=True))
    season_match = re.search(r"\b(20\d{2}/20\d{2})\b", competition)
    title = clean(soup.title.get_text(" ", strip=True)) if soup.title else None
    return {
        "external_id": str(meid),
        "source_url": MATCH_URL.format(meid=meid),
        "title": title,
        "league": competition,
        "season": season_match.group(1) if season_match else None,
        "round": int(round_match.group(1)) if round_match else None,
        "leg": int(round_match.group(2)) if round_match else None,
        "match_date": date_match.group(1) if date_match else None,
        "home_team": home_team,
        "away_team": away_team,
        "home_code": home_code,
        "away_code": away_code,
        "home_scheme": "letters" if home_is_letters else "numbers",
        "away_scheme": "numbers" if away_is_numbers else "letters",
        "team_result": team_result,
        "raw_text": raw_text,
        "players": players,
        "games": games,
        "has_doubles": sum(g["game_type"] == "doubles" for g in games) >= 2,
        "player_count": len(players),
        "singles_count": sum(g["game_type"] == "singles" for g in games),
        "doubles_count": sum(g["game_type"] == "doubles" for g in games),
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
            setattr(match, field, parsed.get(field))
        match.parsed_at = datetime.utcnow()
        match.players.clear()
        match.games.clear()
        for player in parsed["players"]:
            match.players.append(MatchPlayer(**{k: player[k] for k in ("name", "external_player_id", "side", "position")}))
        for game in parsed["games"]:
            match.games.append(MatchGame(**{k: game.get(k) for k in ("sequence", "game_type", "home_position", "away_position", "home_player", "away_player", "result", "sets", "raw_row")}))


def import_one(meid: int) -> dict:
    create_all()
    html, status, content_type, url = fetch_match(meid)
    parsed = parse_match(html, meid)
    # The current endpoint is specifically the OÖTTV Ergebnisdienst. We still
    # require a real 4-player report with both doubles before saving it.
    is_valid_4_player = parsed["player_count"] == 8 and parsed["singles_count"] == 12 and parsed["doubles_count"] == 2
    if is_valid_4_player:
        save_match(meid, html, status, content_type, url, parsed)
    return {"parsed": parsed, "saved": is_valid_4_player, "valid_4_player": is_valid_4_player}


def main() -> None:
    parser = argparse.ArgumentParser(description="Import one OÖTTV XTTV match into PostgreSQL")
    parser.add_argument("meid", type=int)
    args = parser.parse_args()
    try:
        print(import_one(args.meid))
    except urllib.error.URLError as exc:
        raise SystemExit(f"XTTV fetch failed for meid={args.meid}: {exc}") from exc


if __name__ == "__main__":
    main()
