from __future__ import annotations

import re
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from bs4 import BeautifulSoup

RC_BASE = "https://www.ratingscentral.com"
USER_AGENT = "TT-Aufstellung/0.1 (+public RatingsCentral OÖTTV event importer)"


def fetch_event_summary(event_id: int) -> tuple[str, str]:
    url = f"{RC_BASE}/EventSummary.php?{urlencode({'EventID': event_id})}"
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    with urlopen(req, timeout=20) as response:
        return response.read().decode("utf-8", errors="replace"), url


def parse_event_summary(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)
    players = {}

    # Summary reports contain rows with: RC ID | Name | Initial ±dev | change | Final ±dev.
    # Parse table rows first, then use a regex fallback for RC's occasionally changing markup.
    for row in soup.find_all("tr"):
        cells = [" ".join(c.stripped_strings) for c in row.find_all(["th", "td"])]
        if len(cells) < 4:
            continue
        m = re.fullmatch(r"(\d+)", cells[0].strip())
        if not m or "," not in cells[1]:
            continue
        players[int(m.group(1))] = {
            "rc_player_id": int(m.group(1)),
            "name": cells[1].strip(),
            "initial": cells[2].strip(),
            "point_change": cells[3].strip(),
            "final": cells[4].strip() if len(cells) > 4 else None,
        }

    if not players:
        pattern = re.compile(r"(?m)^\s*(\d{3,7})\s*\|\s*([^|\n]+,\s*[^|\n]+)\s*\|\s*([^|\n]+)\s*\|\s*([^|\n]+)\s*\|\s*([^|\n]+)")
        for m in pattern.finditer(text):
            rc_id = int(m.group(1))
            players[rc_id] = {
                "rc_player_id": rc_id,
                "name": m.group(2).strip(),
                "initial": m.group(3).strip(),
                "point_change": m.group(4).strip(),
                "final": m.group(5).strip(),
            }

    return {"players": list(players.values())}


def debug_event(event_id: int) -> dict:
    html, url = fetch_event_summary(event_id)
    parsed = parse_event_summary(html)
    sample = [p for p in parsed["players"] if "Wittinghofer" in p["name"]]
    return {
        "ok": True,
        "event_id": event_id,
        "url": url,
        "html_bytes": len(html.encode("utf-8")),
        "player_count": len(parsed["players"]),
        "wittinghofer_matches": sample,
        "first_5": parsed["players"][:5],
    }
