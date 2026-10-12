"""Fetch XTTV 421 schedule and group MEIDs by Spielrunde from match report headers."""
from __future__ import annotations

import re
import urllib.request
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup
from sqlalchemy import text

from app.db import SessionLocal
from app.xttv_import import fetch_match
from app.xttv_parser import parse_match

BASE = "https://oettv.xttv.at/ed/"
SJID = 26
LID = 421  # may need adjustment


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "TT-Aufstellung/0.2", "Referer": BASE})
    with urllib.request.urlopen(req, timeout=90) as response:
        return response.read().decode(response.headers.get_content_charset() or "iso-8859-1", errors="replace")


def find_lid() -> int:
    html = fetch(f"{BASE}index.php?oid=191&sjid={SJID}")
    for anchor in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        label = anchor.get_text(" ", strip=True)
        if label.startswith("421 "):
            params = parse_qs(urlparse(urljoin(BASE, anchor["href"])).query)
            lid = params.get("lid", [None])[0]
            if lid and str(lid).isdigit():
                return int(lid)
    raise SystemExit("421 lid not found")


def schedule_meids(lid: int) -> list[int]:
    url = f"{BASE}index.php?oid=191&sjid={SJID}&do=spiele&showMenu=Y&lid={lid}&zeit=all&limit=500"
    html = fetch(url)
    seen: set[int] = set()
    for meid_raw in re.findall(r"meid=(\d+)", html, re.I):
        seen.add(int(meid_raw))
    return sorted(seen)


def main() -> None:
    lid = find_lid()
    print("lid", lid)
    meids = schedule_meids(lid)
    print("schedule meids", len(meids), meids)

    by_round: dict[tuple[int | None, int | None], list[int]] = {}
    with SessionLocal() as db:
        for meid in meids:
            row = db.execute(
                text(
                    "SELECT id, home_team, away_team, team_result FROM xttv_matches "
                    "WHERE external_id = :e"
                ),
                {"e": str(meid)},
            ).mappings().first()
            html, *_ = fetch_match(meid)
            parsed = parse_match(html, meid)
            key = (parsed.get("round"), parsed.get("leg"))
            by_round.setdefault(key, []).append(meid)
            tr = row["team_result"] if row else "?"
            print(
                f"  meid={meid} R{parsed.get('round')}/DG{parsed.get('leg')} "
                f"{parsed.get('home_team')} vs {parsed.get('away_team')} result={tr}"
            )

    print("\nBy round:")
    for key in sorted(by_round.keys(), key=lambda k: (k[0] or 0, k[1] or 0)):
        print(f"  R{key[0]}/DG{key[1]}:", by_round[key])


if __name__ == "__main__":
    main()
