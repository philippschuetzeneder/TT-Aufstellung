from __future__ import annotations

import re
from bs4 import BeautifulSoup

MATCH_URL = "https://oettv.xttv.at/ed/spielbericht.inc.php?meid={meid}"


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _cells(table):
    return [[clean(c.get_text(" ", strip=True)) for c in tr.find_all(["th", "td"])]
            for tr in table.find_all("tr") if tr.find_all(["th", "td"])]


def _team(cell: str):
    m = re.match(r"(.+?)\s*\(([^)]+)\)", cell.strip())
    if not m:
        raise ValueError(f"Could not parse team cell: {cell!r}")
    return clean(m.group(1)), clean(m.group(2))


def _player(cell: str):
    m = re.match(r"([A-D1-4]):\s*PassNr\s+(\d+)\s+(.+)$", cell)
    if not m:
        raise ValueError(f"Could not parse player cell: {cell!r}")
    return m.group(1), clean(m.group(3)), m.group(2)


def _pairs(cell: str):
    result = {}
    pattern = re.compile(
        r"Doppel\s*\((\d+)\):\s*(\d+)\s*/\s*(\d+)\s+(.+?)(?=\s+Doppel\s*\(|$)", re.I
    )
    for m in pattern.finditer(cell):
        result[int(m.group(1))] = {
            "sequence": int(m.group(1)),
            "pass_numbers": [m.group(2), m.group(3)],
            "players": clean(m.group(4)),
        }
    return result


def _result_for_sides(raw_left: int, raw_right: int, winner_code: str, home_code: str, away_code: str, vertical_side: str):
    # XTTV prints the score from the vertical lineup's perspective. Therefore
    # the score must be reversed only when the vertical lineup is the away team.
    if vertical_side == "home":
        result = f"{raw_left}:{raw_right}"
    else:
        result = f"{raw_right}:{raw_left}"

    if winner_code == home_code:
        return result, "home"
    if winner_code == away_code:
        return result, "away"
    raise ValueError(f"Unknown winner code {winner_code!r}; expected {home_code!r} or {away_code!r}")


def parse_match(html: str, meid: int) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    if len(tables) < 2:
        raise ValueError("XTTV report does not contain the expected two tables")

    header = _cells(tables[0])
    if len(header) < 2 or len(header[0]) < 5 or len(header[1]) < 5:
        raise ValueError("XTTV header layout is not recognized")

    home_label, away_label = header[0][1], header[1][1]
    home_is_letters = bool(re.search(r"A-D", home_label))
    home_is_numbers = bool(re.search(r"1-4", home_label))
    away_is_letters = bool(re.search(r"A-D", away_label))
    away_is_numbers = bool(re.search(r"1-4", away_label))

    # 3-player formats are intentionally not imported.
    if re.search(r"A-C", home_label) or re.search(r"A-C", away_label):
        raise ValueError("XTTV 3-player format (A-C) is not supported")
    if re.search(r"1-3", home_label) or re.search(r"1-3", away_label):
        raise ValueError("XTTV 3-player format (1-3) is not supported")

    if home_is_letters == home_is_numbers or away_is_letters == away_is_numbers:
        raise ValueError(f"Could not determine the A-D / 1-4 assignment from XTTV header: home={home_label!r}, away={away_label!r}")
    if home_is_letters == away_is_letters:
        raise ValueError(f"Could not determine the A-D / 1-4 assignment from XTTV header: home={home_label!r}, away={away_label!r}")

    home_scheme = "letters" if home_is_letters else "numbers"
    away_scheme = "letters" if away_is_letters else "numbers"
    horizontal_side = "home" if home_scheme == "letters" else "away"
    vertical_side = "away" if horizontal_side == "home" else "home"
    horizontal_positions = list("ABCD")
    vertical_positions = list("1234")

    home_team, home_code = _team(header[0][2])
    away_team, away_code = _team(header[1][2])
    team_result = header[0][4]
    date_match = re.search(r"Datum:\s*(\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2})", clean(" ".join(" ".join(r) for r in header[:2])))
    round_match = re.search(r"Runde:\s*(\d+)\s*\(Durchgang\s*(\d+)\)", clean(" ".join(" ".join(r) for r in header[:2])))

    grid = _cells(tables[1])
    if len(grid) < 6:
        raise ValueError(f"Expected at least 6 rows in XTTV match table, got {len(grid)}")
    competition = grid[0][0]

    players = []
    by_pos = {}

    # XTTV always places the horizontal (A-D) lineup in grid[0]
    # and the vertical (1-4) lineup in rows 1-4.
    for cell in grid[0]:
        if re.match(r"^[A-D]:\s*PassNr\s+\d+\s+", cell):
            pos, name, pid = _player(cell)
            p = {"name": name, "external_player_id": pid, "side": horizontal_side, "position": pos}
            players.append(p)
            by_pos[(horizontal_side, pos)] = p

    if len([p for p in players if p["side"] == horizontal_side]) != 4:
        raise ValueError(f"XTTV horizontal lineup does not contain exactly four players: {players!r}")

    games = []
    for row in grid[1:5]:
        if not row:
            continue
        pos, name, pid = _player(row[0])
        p = {"name": name, "external_player_id": pid, "side": vertical_side, "position": pos}
        players.append(p)
        by_pos[(vertical_side, pos)] = p

        for idx, cell in enumerate(row[1:5]):
            if not cell or idx >= 4:
                continue
            horizontal_pos = horizontal_positions[idx]
            m = re.search(r"\((\d+)\)\s+(\d+):(\d+)\s+([A-Za-z0-9]+)", cell)
            if not m:
                continue
            seq = int(m.group(1))
            raw_left, raw_right = int(m.group(2)), int(m.group(3))
            winner_code = m.group(4)
            result, winner_side = _result_for_sides(raw_left, raw_right, winner_code, home_code, away_code, vertical_side)

            horizontal_player = by_pos.get((horizontal_side, horizontal_pos))
            vertical_player = by_pos.get((vertical_side, pos))
            if not horizontal_player or not vertical_player:
                raise ValueError(
                    f"XTTV position mapping failed: meid={meid}, sequence={seq}, "
                    f"horizontal_side={horizontal_side}, horizontal_pos={horizontal_pos}, "
                    f"vertical_side={vertical_side}, vertical_pos={pos}, "
                    f"known_positions={sorted(by_pos.keys())}"
                )

            home_player = horizontal_player if horizontal_side == "home" else vertical_player
            away_player = vertical_player if vertical_side == "away" else horizontal_player
            games.append({
                "sequence": seq,
                "game_type": "singles",
                "home_position": home_player["position"],
                "away_position": away_player["position"],
                "home_player": home_player["name"],
                "away_player": away_player["name"],
                "result": result,
                "sets": result,
                "winner_side": winner_side,
                "raw_row": cell,
            })

    if len([p for p in players if p["side"] == vertical_side]) != 4:
        raise ValueError("XTTV vertical lineup does not contain exactly four players")

    horizontal_pairs = {}
    for cell in grid[0]:
        if "Doppel" in cell:
            horizontal_pairs.update(_pairs(cell))
    vertical_pairs = {}
    for cell in grid[5]:
        if "Doppel" in cell:
            vertical_pairs.update(_pairs(cell))

    for cell in grid[5]:
        for m in re.finditer(r"\((5|10)\)\s+(\d+):(\d+)\s+([A-Za-z0-9]+)", cell):
            seq = int(m.group(1))
            raw_left, raw_right = int(m.group(2)), int(m.group(3))
            winner_code = m.group(4)
            result, winner_side = _result_for_sides(raw_left, raw_right, winner_code, home_code, away_code, vertical_side)
            hp = horizontal_pairs.get(seq)
            vp = vertical_pairs.get(seq)
            home_pair = hp["players"] if horizontal_side == "home" and hp else vp["players"] if vertical_side == "home" and vp else None
            away_pair = hp["players"] if horizontal_side == "away" and hp else vp["players"] if vertical_side == "away" and vp else None
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
    singles = [g for g in games if g["game_type"] == "singles"]
    doubles = [g for g in games if g["game_type"] == "doubles"]

    # XTTV uses several valid match formats in this league. We require four
    # players per team and both doubles, but do not reject a match solely
    # because it ended after a different number of singles.
    if len(singles) not in (8, 10, 12) or len(doubles) != 2:
        raise ValueError(f"Unexpected game count: singles={len(singles)}, doubles={len(doubles)}")

    home_wins = sum(g["winner_side"] == "home" for g in games)
    away_wins = sum(g["winner_side"] == "away" for g in games)
    score_match = re.fullmatch(r"(\d+)\s*:\s*(\d+)", team_result)
    if score_match and (home_wins, away_wins) != (int(score_match.group(1)), int(score_match.group(2))):
        raise ValueError(f"Parsed game results do not match XTTV team result {team_result}: parsed {home_wins}:{away_wins}")

    season = re.search(r"\b(20\d{2}/20\d{2})\b", competition)
    return {
        "external_id": str(meid),
        "source_url": MATCH_URL.format(meid=meid),
        "title": clean(soup.title.get_text(" ", strip=True)) if soup.title else None,
        "league": competition,
        "season": season.group(1) if season else None,
        "round": int(round_match.group(1)) if round_match else None,
        "leg": int(round_match.group(2)) if round_match else None,
        "match_date": date_match.group(1) if date_match else None,
        "home_team": home_team,
        "away_team": away_team,
        "home_code": home_code,
        "away_code": away_code,
        "home_scheme": home_scheme,
        "away_scheme": away_scheme,
        "team_result": team_result,
        "players": players,
        "games": games,
        "player_count": len(players),
        "singles_count": len(singles),
        "doubles_count": len(doubles),
        "has_doubles": True,
        "raw_text": clean(soup.get_text(" ", strip=True)),
    }
