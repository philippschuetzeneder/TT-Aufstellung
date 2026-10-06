"""Compare XTTV sjid=26 round-1 schedule MEIDs vs local DB (all leagues)."""
from __future__ import annotations

import re
import sys
import urllib.request
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup
from sqlalchemy import text

from app.db import SessionLocal, create_all

BASE = "https://oettv.xttv.at/ed/"
SJID = 26
def fetch(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "TT-Aufstellung/0.2", "Referer": BASE},
    )
    with urllib.request.urlopen(req, timeout=90) as response:
        return response.read().decode(
            response.headers.get_content_charset() or "iso-8859-1",
            errors="replace",
        )


def league_index() -> list[tuple[int, str]]:
    html = fetch(f"{BASE}index.php?oid=191&sjid={SJID}")
    out: list[tuple[int, str]] = []
    seen: set[int] = set()
    for anchor in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        if "lid=" not in anchor["href"].lower():
            continue
        params = parse_qs(urlparse(urljoin(BASE, anchor["href"])).query)
        lid_raw = params.get("lid", [None])[0]
        if not lid_raw or not str(lid_raw).isdigit():
            continue
        lid = int(lid_raw)
        if lid in seen:
            continue
        seen.add(lid)
        out.append((lid, anchor.get_text(" ", strip=True)))
    return sorted(out, key=lambda x: x[1])


def schedule_meids(lid: int) -> list[int]:
    """MEIDs linked on the league schedule (XTTV only links played/entered games)."""
    url = (
        f"{BASE}index.php?oid=191&sjid={SJID}&do=spiele&showMenu=Y&lid={lid}"
        f"&zeit=all&limit=500"
    )
    html = fetch(url)
    seen: set[int] = set()
    for meid_raw in re.findall(r"meid=(\d+)", html, re.I):
        meid = int(meid_raw)
        seen.add(meid)
    return sorted(seen)


def db_meids(meids: list[int]) -> set[int]:
    if not meids:
        return set()
    create_all()
    bind = {f"m{i}": str(m) for i, m in enumerate(meids)}
    placeholders = ", ".join(f":m{i}" for i in range(len(meids)))
    with SessionLocal() as session:
        rows = session.execute(
            text(
                f"SELECT external_id FROM xttv_matches "
                f"WHERE external_id IN ({placeholders})"
            ),
            bind,
        ).fetchall()
    return {int(r[0]) for r in rows if str(r[0]).isdigit()}


def main() -> None:
    focus = sys.argv[1] if len(sys.argv) > 1 else ""
    leagues = league_index()
    total_sched = 0
    total_missing = 0
    problems: list[str] = []

    for lid, name in leagues:
        if focus and focus not in name:
            continue
        try:
            sched = schedule_meids(lid)
        except Exception as exc:
            problems.append(f"{name} (lid={lid}): fetch error {exc}")
            continue
        if not sched:
            continue
        in_db = db_meids(sched)
        missing = sorted(set(sched) - in_db)
        total_sched += len(sched)
        total_missing += len(missing)
        status = "OK" if not missing else f"MISSING {len(missing)}/{len(sched)}"
        print(f"{status}  {name}  lid={lid}  schedule_meids={len(sched)}")
        for meid in missing[:8]:
            print(f"    - meid {meid}")
        if len(missing) > 8:
            print(f"    ... +{len(missing) - 8} more")

    print(f"\nSUMMARY schedule_meids: on_xttv={total_sched} missing_in_db={total_missing}")
    print("(XTTV verlinkt MEIDs erst bei eingetragenen Ergebnissen; offene Runde-1-Spiele ohne Link zählen nicht.)")
    if problems:
        print("ERRORS:", *problems, sep="\n  ")


if __name__ == "__main__":
    main()
