"""Discover MEIDs from XTTV league schedules and import missing Spielberichte."""

from __future__ import annotations

import logging
import re
import sys
import time
import urllib.request
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup

from .xttv_db_import import (
    MAX_IMPORT,
    REQUEST_DELAY,
    _classify_meid,
    _is_imported,
    _try_import_classified,
    create_all,
    get_target_seasons,
)

logger = logging.getLogger(__name__)

BASE = "https://oettv.xttv.at/ed/"
SEASON_TO_SJID: dict[str, int] = {
    "2026/2027": 26,
    "2025/2026": 25,
    "2024/2025": 24,
    "2023/2024": 23,
}


def _fetch_html(url: str, *, timeout: int = 90) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; TT-Aufstellung/0.2)",
            "Accept": "text/html",
            "Referer": BASE,
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode(
            response.headers.get_content_charset() or "iso-8859-1",
            errors="replace",
        )


def parse_meids_from_html(html: str) -> list[int]:
    """Extract unique MEIDs from any XTTV HTML (schedule or index)."""
    seen: set[int] = set()
    ordered: list[int] = []
    for meid_raw in re.findall(r"meid=(\d+)", html, re.I):
        meid = int(meid_raw)
        if meid in seen:
            continue
        seen.add(meid)
        ordered.append(meid)
    return ordered


def league_index(sjid: int) -> list[tuple[int, str]]:
    """Return (lid, label) for all leagues in a season index."""
    html = _fetch_html(f"{BASE}index.php?oid=191&sjid={sjid}")
    out: list[tuple[int, str]] = []
    seen: set[int] = set()
    for anchor in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        href = anchor["href"]
        if "lid=" not in href.lower():
            continue
        params = parse_qs(urlparse(urljoin(BASE, href)).query)
        lid_raw = params.get("lid", [None])[0]
        if not lid_raw or not str(lid_raw).isdigit():
            continue
        lid = int(lid_raw)
        if lid in seen:
            continue
        seen.add(lid)
        label = anchor.get_text(" ", strip=True)
        out.append((lid, label))
    return sorted(out, key=lambda x: x[1])


def schedule_meids_for_lid(lid: int, sjid: int, *, limit: int = 500) -> list[int]:
    url = (
        f"{BASE}index.php?oid=191&sjid={sjid}&do=spiele&showMenu=Y&lid={lid}"
        f"&zeit=all&limit={limit}"
    )
    html = _fetch_html(url)
    return parse_meids_from_html(html)


def sjids_for_scope(scope: str) -> list[int] | None:
    """Return sjid list for schedule discovery; None = all rolling target seasons."""
    scope = (scope or "current").strip().lower()
    if scope == "all":
        return None
    seasons = get_target_seasons()
    latest = max(seasons, key=lambda label: tuple(int(x) for x in label.split("/")))
    sid = SEASON_TO_SJID.get(latest)
    return [sid] if sid is not None else None


def collect_schedule_meids(*, sjids: list[int] | None = None) -> dict:
    """Gather all MEIDs linked on XTTV schedules for target seasons."""
    seasons = get_target_seasons()
    if sjids is None:
        sjids = []
        for season in sorted(seasons):
            sid = SEASON_TO_SJID.get(season)
            if sid is not None:
                sjids.append(sid)
        sjids = sorted(set(sjids))

    by_season: dict[int, dict] = {}
    all_meids: set[int] = set()
    leagues_checked = 0

    for sjid in sjids:
        season_label = next((s for s, v in SEASON_TO_SJID.items() if v == sjid), str(sjid))
        try:
            leagues = league_index(sjid)
        except Exception as exc:
            logger.warning("XTTV league index sjid=%s failed: %s", sjid, exc)
            by_season[sjid] = {"error": str(exc), "leagues": 0, "meids": 0}
            continue

        season_meids: set[int] = set()
        total_leagues = len(leagues)
        for idx, (lid, label) in enumerate(leagues, start=1):
            try:
                meids = schedule_meids_for_lid(lid, sjid)
                season_meids.update(meids)
                leagues_checked += 1
            except Exception as exc:
                logger.warning("XTTV schedule lid=%s sjid=%s failed: %s", lid, sjid, exc)
            if idx == 1 or idx == total_leagues or idx % 15 == 0:
                print(
                    f"[xttv-schedule] sjid={sjid} ({season_label}) Liga {idx}/{total_leagues}: "
                    f"{label[:50]!s} … MEIDs bisher {len(season_meids)}",
                    file=sys.stderr,
                    flush=True,
                )
            time.sleep(REQUEST_DELAY)

        all_meids.update(season_meids)
        by_season[sjid] = {
            "season": season_label,
            "leagues": len(leagues),
            "meids": len(season_meids),
        }

    return {
        "sjids": sjids,
        "leagues_checked": leagues_checked,
        "meids_total": len(all_meids),
        "meids": sorted(all_meids),
        "by_season": by_season,
    }


def import_meid_batch(
    missing: list[int],
    *,
    limit: int,
) -> dict:
    """Try to import up to ``limit`` MEIDs from ``missing`` (newest-first list)."""
    limit = min(max(int(limit), 0), MAX_IMPORT)
    imported_ids: list[int] = []
    imported_details: list[dict] = []
    import_failures: list[dict] = []
    checked = errors = skipped_not_ready = 0

    for meid in missing:
        if len(imported_ids) >= limit:
            break
        checked += 1
        cls = _classify_meid(meid, check_db=True)
        if cls.get("status") == "already_imported":
            continue
        if cls.get("miss") and cls.get("reason") == "not_yet":
            skipped_not_ready += 1
            continue
        if cls.get("miss"):
            continue
        import_succeeded = _try_import_classified(
            cls, imported_ids, imported_details, import_failures, limit,
        )
        if cls.get("status") == "error":
            errors += 1
        if cls.get("status") in {"parse_error", "invalid_report"}:
            errors += 1
        if not import_succeeded and cls.get("importable"):
            errors += 1
        time.sleep(REQUEST_DELAY)

    return {
        "checked": checked,
        "imported": len(imported_ids),
        "imported_meids": imported_ids,
        "imported_details": imported_details,
        "import_failures": import_failures,
        "errors": errors,
        "skipped_not_ready": skipped_not_ready,
    }


def import_missing_schedule_reports(
    *,
    limit: int = MAX_IMPORT,
    schedule_scope: str = "current",
) -> dict:
    """Import Spielberichte for schedule MEIDs that are not yet in the DB."""
    create_all()
    discovery = collect_schedule_meids(sjids=sjids_for_scope(schedule_scope))
    schedule_meids = discovery["meids"]
    missing = sorted(
        (m for m in schedule_meids if not _is_imported(m)),
        reverse=True,
    )
    batch = import_meid_batch(missing, limit=limit)

    return {
        "ok": True,
        "mode": "schedule",
        "schedule_scope": schedule_scope,
        "discovery": {
            "leagues_checked": discovery["leagues_checked"],
            "meids_on_schedules": discovery["meids_total"],
            "missing_in_db": len(missing),
            "by_season": discovery["by_season"],
            "sjids": discovery["sjids"],
        },
        **batch,
    }


def drain_schedule_backlog(
    *,
    schedule_scope: str = "all",
    batch_size: int = MAX_IMPORT,
    max_rounds: int = 50,
) -> dict:
    """Repeat schedule imports until no new reports or rounds exhausted (one discovery pass)."""
    create_all()
    print(
        f"[xttv-schedule] Index/Spielpläne laden (scope={schedule_scope}) …",
        file=sys.stderr,
        flush=True,
    )
    discovery = collect_schedule_meids(sjids=sjids_for_scope(schedule_scope))
    missing = sorted(
        (m for m in discovery["meids"] if not _is_imported(m)),
        reverse=True,
    )
    print(
        f"[xttv-schedule] {discovery['meids_total']} MEIDs auf Plänen, "
        f"{len(missing)} fehlen in DB — Import startet …",
        file=sys.stderr,
        flush=True,
    )
    total_imported: list[int] = []
    rounds = 0
    total_errors = 0
    last_batch: dict = {}

    while missing and rounds < max_rounds:
        rounds += 1
        print(
            f"[xttv-schedule] Runde {rounds}: noch {len(missing)} Kandidaten …",
            file=sys.stderr,
            flush=True,
        )
        batch = import_meid_batch(missing, limit=batch_size)
        last_batch = batch
        total_errors += int(batch.get("errors") or 0)
        print(
            f"[xttv-schedule] Runde {rounds}: +{batch.get('imported', 0)} importiert, "
            f"geprüft {batch.get('checked', 0)}",
            file=sys.stderr,
            flush=True,
        )
        if not batch.get("imported_meids"):
            break
        total_imported.extend(batch["imported_meids"])
        imported_set = set(total_imported)
        missing = [m for m in missing if m not in imported_set and not _is_imported(m)]

    return {
        "ok": True,
        "mode": "schedule_drain",
        "schedule_scope": schedule_scope,
        "rounds": rounds,
        "imported": len(total_imported),
        "imported_meids": total_imported,
        "errors": total_errors,
        "discovery": {
            "leagues_checked": discovery["leagues_checked"],
            "meids_on_schedules": discovery["meids_total"],
            "missing_remaining": len(missing),
            "by_season": discovery["by_season"],
        },
        "last_batch": last_batch,
    }
