from __future__ import annotations

import re
from bs4 import BeautifulSoup

MATCH_URL = "https://oettv.xttv.at/ed/spielbericht.inc.php?meid={meid}"
WALKOVER_LABEL = "w.o."


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def normalize_team_name(name: str) -> str:
    """Collapse whitespace and normalize common dash variants for stable team keys."""
    value = clean(name)
    return value.replace("\u2013", "-").replace("\u2014", "-").replace("\u2212", "-")


def _cells(table):
    return [[clean(c.get_text(" ", strip=True)) for c in tr.find_all(["th", "td"])]
            for tr in table.find_all("tr") if tr.find_all(["th", "td"])]


def _team(cell: str):
    m = re.match(r"(.+?)\s*\(([^)]+)\)", cell.strip())
    if not m:
        raise ValueError(f"Could not parse team cell: {cell!r}")
    return normalize_team_name(m.group(1)), clean(m.group(2))


def _is_walkover_lineup_cell(cell: str) -> bool:
    return bool(re.match(r"^[A-D1-4]:\s*w\.o\.?\s*$", cell.strip(), re.I))


def _player(cell: str):
    m = re.match(r"([A-D1-4]):\s*PassNr\s+(\d+)\s+(.+)$", cell)
    if m:
        return m.group(1), clean(m.group(3)), m.group(2)
    m = re.match(r"([A-D1-4]):\s*(.+)$", cell.strip())
    if m and not _is_walkover_lineup_cell(cell):
        name = clean(m.group(2))
        if name:
            return m.group(1), name, None
    raise ValueError(f"Could not parse player cell: {cell!r}")


def _parse_lineup_cell(cell: str) -> tuple[str, dict]:
    stripped = cell.strip()
    if _is_walkover_lineup_cell(stripped):
        position = stripped.split(":")[0].strip()
        return position, {
            "name": WALKOVER_LABEL,
            "external_player_id": None,
            "walkover": True,
            "position": position,
        }
    position, name, player_id = _player(stripped)
    return position, {
        "name": name,
        "external_player_id": player_id,
        "walkover": False,
        "position": position,
    }


def _slot_label(slot: dict) -> str:
    return WALKOVER_LABEL if slot.get("walkover") else slot["name"]


def _pairs(cell: str):
    result = {}
    walkover_pattern = re.compile(r"Doppel\s*\((\d+)\):\s*w\.o\.\s*w\.o\.", re.I)
    for m in walkover_pattern.finditer(cell):
        result[int(m.group(1))] = {
            "sequence": int(m.group(1)),
            "walkover": True,
        }
    pattern = re.compile(
        r"Doppel\s*\((\d+)\):\s*(\d+)\s*/\s*(\d+)\s+(.+?)(?=\s+Doppel\s*\(|$)", re.I
    )
    for m in pattern.finditer(cell):
        result[int(m.group(1))] = {
            "sequence": int(m.group(1)),
            "pass_numbers": [m.group(2), m.group(3)],
            "players": clean(m.group(4)),
            "walkover": False,
        }
    return result


def _resolve_winner_code(winner_code: str, home_code: str, away_code: str) -> str:
    if winner_code == home_code:
        return "home"
    if winner_code == away_code:
        return "away"
    matches = []
    if home_code and home_code.startswith(winner_code):
        matches.append("home")
    if away_code and away_code.startswith(winner_code):
        matches.append("away")
    if len(matches) == 1:
        return matches[0]
    raise ValueError(f"Unknown winner code {winner_code!r}; expected {home_code!r} or {away_code!r}")


def _result_for_sides(raw_left: int, raw_right: int, winner_code: str, home_code: str, away_code: str, vertical_side: str):
    # XTTV prints the score from the vertical lineup's perspective. Therefore
    # the score must be reversed only when the vertical lineup is the away team.
    if vertical_side == "home":
        result = f"{raw_left}:{raw_right}"
    else:
        result = f"{raw_right}:{raw_left}"
    return result, _resolve_winner_code(winner_code, home_code, away_code)


def _expected_singles_count(home_wins: int, away_wins: int) -> int:
    """Return the required number of singles for the fixed 4-player format.

    There are always exactly two doubles. Singles stop as soon as one team
    reaches eight singles wins, so the valid final team scores are the
    mirrored 8:2..8:6 results, 9:1, 10:0 and 7:7.
    """
    high = max(home_wins, away_wins)
    low = min(home_wins, away_wins)

    if high == 8 and low in (2, 3, 4, 5, 6):
        return low + 6
    if high == 9 and low == 1:
        return 8
    if high == 10 and low == 0:
        return 8
    if (home_wins, away_wins) == (7, 7):
        return 12
    raise ValueError(
        f"Invalid XTTV team result {home_wins}:{away_wins}; "
        "expected one of 10:0, 9:1, 8:2, 8:3, 8:4, 8:5, 8:6 or 7:7, "
        "including mirrored results"
    )


def _validate_walkover_game_structure(games: list[dict], home_wins: int, away_wins: int) -> None:
    """Relaxed validation for reports with walkover lineup slots."""
    singles = [g for g in games if g.get("game_type") == "singles"]
    doubles = [g for g in games if g.get("game_type") == "doubles"]
    if len(singles) < 4:
        raise ValueError(f"Walkover report needs at least 4 singles, got {len(singles)}")
    if len(doubles) > 2:
        raise ValueError(f"Walkover report has too many doubles: {len(doubles)}")
    sequences = [g["sequence"] for g in games]
    if len(sequences) != len(set(sequences)):
        raise ValueError("Duplicate game sequences in walkover report")
    for game in games:
        if game.get("winner_side") not in {"home", "away"}:
            raise ValueError(f"Invalid winner_side in walkover game {game.get('sequence')}")
    expected_singles = _expected_singles_count(home_wins, away_wins)
    if len(singles) > expected_singles:
        raise ValueError(
            f"Walkover singles count {len(singles)} exceeds expected {expected_singles} "
            f"for team result {home_wins}:{away_wins}"
        )


def _validate_game_structure(games: list[dict], home_wins: int, away_wins: int) -> None:
    """Validate the structural rules of a complete XTTV 4-player report."""
    singles = [g for g in games if g.get("game_type") == "singles"]
    doubles = [g for g in games if g.get("game_type") == "doubles"]
    expected_singles = _expected_singles_count(home_wins, away_wins)

    if len(doubles) != 2:
        raise ValueError(f"Unexpected double count: doubles={len(doubles)}; exactly two doubles are required")
    if len(singles) != expected_singles:
        raise ValueError(
            f"Unexpected game count for result {home_wins}:{away_wins}: "
            f"singles={len(singles)}, expected={expected_singles}, doubles={len(doubles)}"
        )

    sequences = [g.get("sequence") for g in games]
    if len(sequences) != len(set(sequences)):
        raise ValueError(f"Duplicate game sequence numbers in XTTV report: {sequences}")
    if set(g.get("sequence") for g in doubles) != {5, 10}:
        raise ValueError("XTTV doubles must be games 5 and 10")
    if any(g.get("sequence") in {5, 10} for g in singles):
        raise ValueError("XTTV doubles sequences 5 and 10 must not also occur as singles")
    if any(g.get("winner_side") not in {"home", "away"} for g in games):
        raise ValueError("XTTV report contains a game without a recognized winner")


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
    header_text = clean(" ".join(" ".join(r) for r in header[:2]))
    date_match = re.search(r"Datum:\s*(\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2})", header_text)
    round_match = re.search(r"Runde:\s*(\d+)\s*\(Durchgang\s*(\d+)\)", header_text)

    grid = _cells(tables[1])
    if len(grid) < 6:
        raise ValueError(f"Expected at least 6 rows in XTTV match table, got {len(grid)}")
    competition = grid[0][0]

    players = []
    by_pos = {}

    for cell in grid[0]:
        if re.match(r"^[A-D]:", cell):
            position, slot = _parse_lineup_cell(cell)
            slot["side"] = horizontal_side
            by_pos[(horizontal_side, position)] = slot
            if not slot.get("walkover"):
                players.append(slot)

    horizontal_slots = [by_pos.get((horizontal_side, pos)) for pos in horizontal_positions]
    if len([slot for slot in horizontal_slots if slot]) != 4:
        raise ValueError(f"XTTV horizontal lineup does not contain exactly four slots: {players!r}")

    for row in grid[1:5]:
        if not row or not re.match(r"^[1-4]:", row[0]):
            continue
        position, slot = _parse_lineup_cell(row[0])
        slot["side"] = vertical_side
        by_pos[(vertical_side, position)] = slot
        if not slot.get("walkover"):
            players.append(slot)

    vertical_slots = [by_pos.get((vertical_side, pos)) for pos in vertical_positions]
    if len([slot for slot in vertical_slots if slot]) != 4:
        raise ValueError("XTTV vertical lineup does not contain exactly four slots")
    if len(by_pos) != 8:
        raise ValueError("XTTV report contains duplicate player positions")
    real_ids = [p["external_player_id"] for p in players if p.get("external_player_id")]
    if len(real_ids) != len(set(real_ids)):
        raise ValueError("XTTV report contains duplicate player IDs")

    games = []
    for row in grid[1:5]:
        if not row or not re.match(r"^[1-4]:", row[0]):
            continue
        position, vertical_slot = _parse_lineup_cell(row[0])
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
            horizontal_slot = by_pos[(horizontal_side, horizontal_pos)]
            home_slot = horizontal_slot if horizontal_side == "home" else vertical_slot
            away_slot = vertical_slot if vertical_side == "away" else horizontal_slot
            games.append({
                "sequence": seq,
                "game_type": "singles",
                "home_position": home_slot["position"],
                "away_position": away_slot["position"],
                "home_player": _slot_label(home_slot),
                "away_player": _slot_label(away_slot),
                "result": result,
                "sets": result,
                "winner_side": winner_side,
                "raw_row": cell,
            })

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
            if horizontal_side == "home":
                home_pair = WALKOVER_LABEL if hp and hp.get("walkover") else (hp.get("players") if hp else None)
                away_pair = WALKOVER_LABEL if vp and vp.get("walkover") else (vp.get("players") if vp else None)
            else:
                home_pair = WALKOVER_LABEL if vp and vp.get("walkover") else (vp.get("players") if vp else None)
                away_pair = WALKOVER_LABEL if hp and hp.get("walkover") else (hp.get("players") if hp else None)
            games.append({"sequence": seq, "game_type": "doubles", "home_position": None, "away_position": None, "home_player": home_pair, "away_player": away_pair, "result": result, "sets": result, "winner_side": winner_side, "raw_row": cell})

    games.sort(key=lambda g: g["sequence"])
    home_wins = sum(g["winner_side"] == "home" for g in games)
    away_wins = sum(g["winner_side"] == "away" for g in games)
    has_walkover = any(slot.get("walkover") for slot in by_pos.values()) or any(
        pair.get("walkover") for pairs in (horizontal_pairs, vertical_pairs) for pair in pairs.values()
    )
    if has_walkover:
        _validate_walkover_game_structure(games, home_wins, away_wins)
    else:
        _validate_game_structure(games, home_wins, away_wins)

    score_match = re.fullmatch(r"(\d+)\s*:\s*(\d+)", team_result)
    if not score_match:
        raise ValueError(f"Could not parse XTTV team result: {team_result!r}")
    if (home_wins, away_wins) != (int(score_match.group(1)), int(score_match.group(2))):
        raise ValueError(f"Parsed game results do not match XTTV team result {team_result}: parsed {home_wins}:{away_wins}")

    singles = [g for g in games if g["game_type"] == "singles"]
    doubles = [g for g in games if g["game_type"] == "doubles"]
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
        "has_doubles": len(doubles) > 0,
        "has_walkover": has_walkover,
        "raw_text": clean(soup.get_text(" ", strip=True)),
    }
