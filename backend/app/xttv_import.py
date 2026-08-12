from datetime import datetime
from urllib.parse import parse_qs, urljoin, urlparse
import re
import urllib.request

from bs4 import BeautifulSoup

from .db import SessionLocal, create_all
from .models import MatchGame, MatchPlayer, RawSourceDocument, XttvMatch

BASE = "https://oettv.xttv.at/ed/"
MATCH_URL = BASE + "spielbericht.inc.php?meid={meid}"


def fetch_match(meid: int) -> tuple[str, int, str, str]:
    url = MATCH_URL.format(meid=meid)
    request = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; TT-Aufstellung/0.1)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "de-AT,de;q=0.9,en;q=0.5",
        "Referer": f"{BASE}index.php",
        "Connection": "keep-alive",
    })
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read()
        charset = response.headers.get_content_charset() or "iso-8859-1"
        return body.decode(charset, errors="replace"), response.status, response.headers.get_content_type(), url


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def player_id_from_link(link) -> str | None:
    href = link.get("href", "")
    params = parse_qs(urlparse(urljoin(BASE, href)).query)
    for key in ("spid", "playerid", "pid", "passnr", "passnummer"):
        if params.get(key):
            return params[key][0]
    return None


def result_value(text: str) -> str | None:
    m = re.search(r"\b(\d{1,2})\s*:\s*(\d{1,2})\b", text)
    return f"{m.group(1)}:{m.group(2)}" if m else None


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


def _extract_team(cell: str) -> tuple[str | None, str | None]:
    m = re.match(r"\s*(.+?)\s*\(([^)]+)\)\s*$", cell)
    return (clean(m.group(1)), clean(m.group(2))) if m else (None, None)


def _extract_position_players(cell: str) -> tuple[str, str, str | None]:
    m = re.match(r"\s*([A-D1-4])\s*:\s*PassNr\s+(\d+)\s+(.+?)\s*$", cell)
    if not m:
        raise ValueError(f"Unexpected XTTV lineup cell: {cell!r}")
    return m.group(1), clean(m.group(3)), m.group(2)


def _extract_doubles(cell: str) -> list[dict]:
    pairs = []
    pattern = re.compile(
        r"Doppel\s*\((\d+)\):\s*(\d+)\s*/\s*(\d+)\s+(.+?)\s+Doppel\s*\((\d+)\):\s*(\d+)\s*/\s*(\d+)\s+(.+?)$",
        re.I,
    )
    m = pattern.search(cell)
    if m:
        pairs.append({"sequence": int(m.group(1)), "pass_numbers": [m.group(2), m.group(3)], "players": clean(m.group(4))})
        pairs.append({"sequence": int(m.group(5)), "pass_numbers": [m.group(6), m.group(7)], "players": clean(m.group(8))})
    return pairs


def parse_match(html: str, meid: int) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    if len(tables) < 2:
        raise ValueError("XTTV report does not contain the expected two tables")

    def table_rows(table):
        out = []
        for tr in table.find_all("tr"):
            cells = [clean(c.get_text(" ", strip=True)) for c in tr.find_all(["th", "td"])]
            if cells:
                out.append(cells)
        return out

    rows = table_rows(tables[0])
    if len(rows) < 2 or len(rows[0]) < 5 or len(rows[1]) < 5:
        raise ValueError("XTTV header layout is not recognized")

    home_header = rows[0][1]
    away_header = rows[1][1]
    home_letter_match = re.search(r"([A-D])\s*-\s*([A-D])", home_header)
    away_letter_match = re.search(r"([A-D])\s*-\s*([A-D])", away_header)
    home_number_match = re.search(r"(\d+)\s*-\s*(\d+)", home_header)
    away_number_match = re.search(r"(\d+)\s*-\s*(\d+)", away_header)

    home_is_letters = bool(home_letter_match)
    away_is_letters = bool(away_letter_match)
    home_is_numbers = bool(home_number_match)
    away_is_numbers = bool(away_number_match)

    # 3-player reports (A-C / 1-3) are intentionally excluded.
    if home_letter_match and home_letter_match.group(1) == "A" and home_letter_match.group(2) == "C":
        raise ValueError("XTTV 3-player format (A-C) is not supported")
    if away_letter_match and away_letter_match.group(1) == "A" and away_letter_match.group(2) == "C":
        raise ValueError("XTTV 3-player format (A-C) is not supported")
    if home_number_match and home_number_match.group(1) == "1" and home_number_match.group(2) == "3":
        raise ValueError("XTTV 3-player format (1-3) is not supported")
    if away_number_match and away_number_match.group(1) == "1" and away_number_match.group(2) == "3":
        raise ValueError("XTTV 3-player format (1-3) is not supported")

    if home_is_letters and away_is_numbers:
        letters_side, numbers_side = "home", "away"
    elif away_is_letters and home_is_numbers:
        letters_side, numbers_side = "away", "home"
    else:
        raise ValueError(f"Could not determine the A-D / 1-4 assignment from XTTV header: home={home_header!r}, away={away_header!r}")

    home_team, home_code = _extract_team(rows[0][2])
    away_team, away_code = _extract_team(rows[1][2])
    if not home_team or not away_team:
        raise ValueError("Could not determine home/away team")

    header_text = clean(" ".join(" ".join(r) for r in rows[:2]))
    date_match = re.search(r"Datum:\s*(\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2})", header_text)
    round_match = re.search(r"Runde:\s*(\d+)\s*\(Durchgang\s*(\d+)\)", header_text)

    table1_rows = table_rows(tables[1])
    if not table1_rows:
        raise ValueError("XTTV match table is empty")
    competition = table1_rows[0][0] if table1_rows[0] else None

    players = []
    player_by_position = {}
    pass_to_player = {}
    doubles_info = {}

    def add_player(cell: str, side: str):
        pos, name, pid = _extract_position_players(cell)
        p = {"name": name, "external_player_id": pid, "side": side, "position": pos}
        players.append(p)
        player_by_position[(side, pos)] = p
        if pid:
            pass_to_player[pid] = p

    for cell in table1_rows[0]:
        if re.match(r"^[A-D]:\s*PassNr", cell):
            add_player(cell, letters_side)
        elif re.match(r"^[1-4]:\s*PassNr", cell):
            add_player(cell, numbers_side)
        elif "Doppel" in cell:
            for pair in _extract_doubles(cell):
                doubles_info[pair["sequence"]] = pair

    for row in table1_rows[1:5]:
        if not row:
            continue
        first = row[0]
        if re.match(r"^[A-D1-4]:\s*PassNr", first):
            pos = first.split(":", 1)[0].strip()
            side = letters_side if pos in "ABCD" else numbers_side
            if (side, pos) not in player_by_position:
                add_player(first, side)

    if len(players) != 8:
        raise ValueError(f"Unexpected player count: {len(players)} (expected 8)")

    games = []
    row_positions = set("ABCD" if letters_side == "home" else "1234")
    column_positions = list("1234" if letters_side == "home" else "ABCD")

    # The first cell identifies the player on the table's vertical axis. The
    # remaining cells are the opponent positions in fixed left-to-right order.
    # This works for both orientations: home A-D / away 1-4 and home 1-4 / away A-D.
    for row in table1_rows[1:5]:
        if not row:
            continue
        first = row[0]
        mpos = re.match(r"^([A-D1-4]):\s*PassNr", first)
        if not mpos:
            continue
        row_pos = mpos.group(1)
        expected_row_positions = "ABCD" if letters_side == "home" else "1234"
        if row_pos not in expected_row_positions:
            continue
        row_side = letters_side if row_pos in "ABCD" else numbers_side
        if row_side == "home":
            home_pos = row_pos
        else:
            away_pos = row_pos

        for col_index, cell in enumerate(row[1:5]):
            if not cell or col_index >= len(column_positions):
                continue
            col_pos = column_positions[col_index]
            if row_side == "home":
                home_pos, away_pos = row_pos, col_pos
            else:
                home_pos, away_pos = col_pos, row_pos

            for match in re.finditer(r"\((\d+)\)\s+(\d+):(\d+)\s+([A-Za-z0-9]+)", cell):
                sequence = int(match.group(1))
                result = f"{match.group(2)}:{match.group(3)}"
                winner_code = match.group(4)
                winner_side = "home" if winner_code == home_code else "away" if winner_code == away_code else None
                home_player = player_by_position.get(("home", home_pos))
                away_player = player_by_position.get(("away", away_pos))
                if not home_player or not away_player:
                    raise ValueError(f"Could not map game {sequence}: home={home_pos}, away={away_pos}")
                games.append({
                    "sequence": sequence,
                    "game_type": "singles",
                    "home_position": home_pos,
                    "away_position": away_pos,
                    "home_player": home_player["name"],
                    "away_player": away_player["name"],
                    "result": result,
                    "sets": result,
                    "winner_side": winner_side,
                    "raw_row": cell,
                })

    if not 10 <= len(games) <= 12:
        raise ValueError(f"Unexpected game count: singles={len(games)}, expected 10-12")

    doubles = []
    all_text = clean(" ".join(" ".join(r) for r in table1_rows))
    for m in re.finditer(r"\((5|10)\)\s+(\d+):(\d+)\s+([A-Za-z0-9]+)", all_text):
        seq = int(m.group(1))
        if any(d["sequence"] == seq for d in doubles):
            continue
        result = f"{m.group(2)}:{m.group(3)}"
        winner_code = m.group(4)
        doubles.append({"sequence": seq, "game_type": "doubles", "home_position": None, "away_position": None,
                        "home_player": None, "away_player": None, "result": result, "sets": result,
                        "winner_side": "home" if winner_code == home_code else "away" if winner_code == away_code else None,
                        "raw_row": all_text})

    if len(doubles) != 2:
        raise ValueError(f"Unexpected doubles count: {len(doubles)}")

    games.extend(doubles)
    games.sort(key=lambda g: g["sequence"])

    team_result = result_value(rows[0][4])
    return {
        "meid": str(meid),
        "league": competition,
        "season": re.search(r"(\d{4}/\d{4})", competition or "").group(1) if re.search(r"(\d{4}/\d{4})", competition or "") else None,
        "round": int(round_match.group(1)) if round_match else None,
        "leg": int(round_match.group(2)) if round_match else None,
        "match_date": date_match.group(1) if date_match else None,
        "home_team": home_team,
        "away_team": away_team,
        "home_code": home_code,
        "away_code": away_code,
        "home_scheme": "letters" if letters_side == "home" else "numbers",
        "away_scheme": "letters" if letters_side == "away" else "numbers",
        "team_result": team_result,
        "players": players,
        "games": games,
        "player_count": len(players),
        "singles_count": len(games),
        "doubles_count": 2,
        "has_doubles": True,
        "raw_text": clean(soup.get_text(" ", strip=True)),
    }
