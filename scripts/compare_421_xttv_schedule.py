"""Fetch XTTV schedule for lid=8278 and compare MEIDs with local DB."""
import re
import urllib.request
from urllib.parse import urljoin, urlparse, parse_qs

from bs4 import BeautifulSoup
from sqlalchemy import text

from app.db import SessionLocal, create_all
from app.player_analysis_service import list_leagues

SCHEDULE_URL = (
    "https://oettv.xttv.at/ed/index.php?"
    "oid=191&sjid=25&order=0&hideColumns=&do=spiele&showMenu=Y&bwid=0&"
    "lid=8278&sprdg=0&sprnr=0&vid=0&tid=0&zeit=all&limit=100"
)
BASE = "https://oettv.xttv.at/ed/"


def fetch_html(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; TT-Aufstellung/0.2)",
            "Accept": "text/html",
            "Referer": BASE,
        },
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        return response.read().decode(
            response.headers.get_content_charset() or "iso-8859-1",
            errors="replace",
        )


def parse_schedule_meids(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "meid" not in href.lower():
            continue
        full = urljoin(BASE, href)
        params = parse_qs(urlparse(full).query)
        meid_raw = params.get("meid", [None])[0]
        if not meid_raw or not str(meid_raw).isdigit():
            continue
        meid = int(meid_raw)
        if meid in seen:
            continue
        seen.add(meid)
        text = " ".join(a.stripped_strings)
        parent_text = " ".join(a.parent.stripped_strings) if a.parent else ""
        rows.append({"meid": meid, "link_text": text, "context": parent_text[:200]})
    return rows


def main():
    create_all()
    html = fetch_html(SCHEDULE_URL)
    schedule = parse_schedule_meids(html)
    print(f"XTTV schedule MEIDs found: {len(schedule)}")

    leagues = list_leagues()["leagues"]
    l421 = next(l for l in leagues if l["name"].startswith("421 "))
    league_name = l421["latest_league"]

    with SessionLocal() as db:
        db_meids = {
            int(row[0])
            for row in db.execute(
                text("SELECT external_id FROM xttv_matches WHERE league = :l"),
                {"l": league_name},
            ).fetchall()
            if str(row[0]).isdigit()
        }
    print(f"DB MEIDs for {league_name}: {len(db_meids)}")

    schedule_ids = {r["meid"] for r in schedule}
    missing = sorted(schedule_ids - db_meids)
    extra = sorted(db_meids - schedule_ids)

    print(f"On XTTV schedule but NOT in DB ({len(missing)}):")
    for meid in missing:
        ctx = next((r for r in schedule if r["meid"] == meid), {})
        print(f"  meid={meid}  {ctx.get('context', '')[:120]}")

    print(f"In DB but not on schedule page ({len(extra)}):")
    for meid in extra[:20]:
        print(f"  meid={meid}")

    if len(extra) > 20:
        print(f"  ... and {len(extra) - 20} more")


if __name__ == "__main__":
    main()
