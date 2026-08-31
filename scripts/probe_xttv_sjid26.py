"""Quick probe of OÖTTV 2026/2027 (sjid=26) schedule pages."""
from __future__ import annotations

import re
import urllib.request
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup

BASE = "https://oettv.xttv.at/ed/"


def fetch(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "TT-Aufstellung/0.2", "Referer": BASE},
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        return response.read().decode(
            response.headers.get_content_charset() or "iso-8859-1",
            errors="replace",
        )


def main() -> None:
    html = fetch(f"{BASE}index.php?oid=191&sjid=26")
    lids: set[int] = set()
    names: dict[int, str] = {}
    for anchor in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        if "lid=" not in anchor["href"].lower():
            continue
        params = parse_qs(urlparse(urljoin(BASE, anchor["href"])).query)
        lid_raw = params.get("lid", [None])[0]
        if lid_raw and str(lid_raw).isdigit():
            lid = int(lid_raw)
            lids.add(lid)
            names[lid] = anchor.get_text(" ", strip=True)

    print(f"leagues sjid=26: {len(lids)}")

    sample_lid = 8815  # 401 RK Linz
    url = (
        f"{BASE}index.php?oid=191&sjid=26&order=0&hideColumns=&do=spiele&showMenu=Y&bwid=0&"
        f"lid={sample_lid}&sprdg=0&sprnr=0&vid=0&tid=0&zeit=all&limit=500"
    )
    schedule = fetch(url)
    meids = set(re.findall(r"meid=(\d+)", schedule, re.I))
    print(f"sample {names.get(sample_lid, sample_lid)}: meids={len(meids)}")
    zeit_idx = schedule.find('name="zeit"')
    if zeit_idx >= 0:
        print("zeit select:", schedule[zeit_idx : zeit_idx + 500].replace("\n", " ")[:500])


if __name__ == "__main__":
    main()
