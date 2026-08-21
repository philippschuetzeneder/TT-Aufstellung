"""Fetch Einzelrangliste players from XTTV league page."""
from __future__ import annotations

import re
import urllib.request
from bs4 import BeautifulSoup

LID = 8277
BASE = f"https://oettv.xttv.at/ed/index.php?lid={LID}"


def fetch_html(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": BASE})
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read().decode(response.headers.get_content_charset() or "iso-8859-1", errors="replace")


def parse_players(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    players: list[dict] = []
    for tr in soup.find_all("tr"):
        text = " ".join(tr.stripped_strings)
        match = re.match(
            r"^(\d+)\.?\s+(.+?)\s+(\d{4,6})\s+([A-Z0-9]{3,6})\s+",
            text,
        )
        if match:
            players.append({
                "rank": int(match.group(1)),
                "name": match.group(2).strip(),
                "pass_id": match.group(3),
                "team_code": match.group(4),
            })
    return players


def main() -> None:
    for suffix in ("", "&do=einzel", "&do=einzelrangliste"):
        url = BASE + suffix
        try:
            html = fetch_html(url)
            players = parse_players(html)
            print(url, "players", len(players))
            for p in players[:5]:
                print(p)
        except Exception as exc:
            print(url, "ERR", exc)


if __name__ == "__main__":
    main()
