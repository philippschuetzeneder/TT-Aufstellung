"""Import walkover (w.o.) match reports that were skipped by the strict 4-player filter.

Scans all OÖTTV league schedules for the last three seasons and imports missing
reports that contain walkover lineup slots.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup

from app.db import create_all
from app.xttv_db_import import (
    _is_imported,
    _is_valid_importable_report,
    import_one,
    REQUEST_DELAY,
    TARGET_SEASONS,
)
from app.xttv_import import fetch_match
from app.xttv_parser import parse_match

BASE = "https://oettv.xttv.at/ed/"
SEASON_SJIDS = {
    23: "2023/2024",
    24: "2024/2025",
    25: "2025/2026",
    26: "2026/2027",
}


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


def discover_lids(sjid: int) -> set[int]:
    html = fetch_html(f"{BASE}index.php?oid=191&sjid={sjid}")
    lids: set[int] = set()
    for anchor in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        if "lid=" not in anchor["href"].lower():
            continue
        params = parse_qs(urlparse(urljoin(BASE, anchor["href"])).query)
        lid_raw = params.get("lid", [None])[0]
        if lid_raw and str(lid_raw).isdigit():
            lids.add(int(lid_raw))
    return lids


def schedule_meids(lid: int, sjid: int) -> set[int]:
    url = (
        f"{BASE}index.php?oid=191&sjid={sjid}&order=0&hideColumns=&do=spiele&showMenu=Y&bwid=0&"
        f"lid={lid}&sprdg=0&sprnr=0&vid=0&tid=0&zeit=all&limit=500"
    )
    html = fetch_html(url)
    meids: set[int] = set()
    for anchor in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        if "meid" not in anchor["href"].lower():
            continue
        params = parse_qs(urlparse(urljoin(BASE, anchor["href"])).query)
        meid_raw = params.get("meid", [None])[0]
        if meid_raw and str(meid_raw).isdigit():
            meids.add(int(meid_raw))
    return meids


def schedule_walkover_meids(lid: int, sjid: int) -> set[int]:
    """MEIDs whose schedule row mentions walkover (w.o.)."""
    url = (
        f"{BASE}index.php?oid=191&sjid={sjid}&order=0&hideColumns=&do=spiele&showMenu=Y&bwid=0&"
        f"lid={lid}&sprdg=0&sprnr=0&vid=0&tid=0&zeit=all&limit=500"
    )
    html = fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")
    meids: set[int] = set()
    for row in soup.find_all("tr"):
        row_text = row.get_text(" ", strip=True)
        if not re.search(r"w\.o\.", row_text, re.I):
            continue
        for anchor in row.find_all("a", href=True):
            if "meid" not in anchor["href"].lower():
                continue
            params = parse_qs(urlparse(urljoin(BASE, anchor["href"])).query)
            meid_raw = params.get("meid", [None])[0]
            if meid_raw and str(meid_raw).isdigit():
                meids.add(int(meid_raw))
    return meids


def is_walkover_html(html: str) -> bool:
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    return bool(re.search(r"[A-D1-4]:\s*w\.o\.", text, re.I))


def try_import_walkover(meid: int, dry_run: bool = False) -> str:
    if _is_imported(meid):
        return "already_imported"
    try:
        html, _, _, _ = fetch_match(meid)
    except urllib.error.HTTPError as exc:
        return f"http_{exc.code}"
    except Exception as exc:
        return type(exc).__name__

    if not is_walkover_html(html):
        return "no_walkover_marker"

    try:
        parsed = parse_match(html, meid)
    except Exception as exc:
        return f"parse_error:{type(exc).__name__}"

    if not parsed.get("has_walkover"):
        return "parsed_without_walkover_flag"
    if parsed.get("season") not in TARGET_SEASONS:
        return f"season_outside_filter:{parsed.get('season')}"
    if not _is_valid_importable_report(parsed):
        return "not_importable"

    if dry_run:
        return "would_import"

    import_one(meid)
    return "imported"


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    parser = argparse.ArgumentParser(description="Backfill walkover XTTV match reports")
    parser.add_argument("--dry-run", action="store_true", help="Only classify, do not import")
    parser.add_argument("--delay", type=float, default=REQUEST_DELAY, help="Delay between MEIDs")
    args = parser.parse_args()

    create_all()
    summary: dict[str, int] = {}
    imported: list[int] = []

    for sjid, season in SEASON_SJIDS.items():
        lids = sorted(discover_lids(sjid))
        print(f"Season {season} (sjid={sjid}): {len(lids)} leagues")
        for lid in lids:
            walkover_meids = schedule_walkover_meids(lid, sjid)
            missing = sorted(meid for meid in walkover_meids if not _is_imported(meid))
            if not missing:
                continue
            print(f"  lid={lid}: {len(missing)} missing walkover schedule MEIDs")
            for meid in missing:
                status = try_import_walkover(meid, dry_run=args.dry_run)
                if status in {"imported", "would_import"}:
                    summary[status] = summary.get(status, 0) + 1
                    imported.append(meid)
                    print(f"    meid={meid} -> {status}")
                elif status not in {"already_imported", "no_walkover_marker", "parsed_without_walkover_flag"}:
                    summary[status] = summary.get(status, 0) + 1
                time.sleep(args.delay)

    print("\nSummary:")
    for status, count in sorted(summary.items()):
        print(f"  {status}: {count}")
    print(f"Imported/would import: {len(imported)}")


if __name__ == "__main__":
    main()
