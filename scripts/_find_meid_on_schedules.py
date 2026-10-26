"""Find which league schedule lists a MEID and sample row text for date parsing."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup

from app.xttv_schedule_import import BASE, _fetch_html, league_index, SEASON_TO_SJID

TARGET = 462244
SJID = SEASON_TO_SJID["2026/2027"]


def main() -> None:
    leagues = league_index(SJID)
    print("leagues", len(leagues))
    for lid, label in leagues:
        if "421" not in label and "Tragwein" not in label and "Kamig" not in label:
            continue
        url = (
            f"{BASE}index.php?oid=191&sjid={SJID}&do=spiele&showMenu=Y&lid={lid}"
            f"&zeit=all&limit=500"
        )
        html = _fetch_html(url)
        if str(TARGET) not in html:
            continue
        print("FOUND lid", lid, label[:80])
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            if f"meid={TARGET}" not in a["href"] and f"MEID={TARGET}" not in a["href"]:
                continue
            row = a.find_parent("tr")
            ctx = row.get_text(" ", strip=True) if row else a.get_text(" ", strip=True)
            print("row text:", ctx[:300])
            print("dates in row:", re.findall(r"\d{2}\.\d{2}\.\d{4}", ctx))
            print("dates 2dig year:", re.findall(r"\d{2}\.\d{2}\.\d{2}", ctx))
            return
    print("MEID not found on filtered leagues; scanning all…")
    for lid, label in leagues:
        url = (
            f"{BASE}index.php?oid=191&sjid={SJID}&do=spiele&showMenu=Y&lid={lid}"
            f"&zeit=all&limit=500"
        )
        html = _fetch_html(url)
        if str(TARGET) in html:
            print("FOUND lid", lid, label[:80])
            return
    print("not found anywhere")


if __name__ == "__main__":
    main()
