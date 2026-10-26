"""Discover MEIDs from XTTV league schedules and import missing Spielberichte."""

from __future__ import annotations

import logging
import os
import re
import sys
import time
import urllib.request
from datetime import date
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


_SCHEDULE_DATE_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")


def _parse_first_date(text: str) -> date | None:
    match = _SCHEDULE_DATE_RE.search(text or "")
    if not match:
        return None
    day, month, year = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _schedule_block_for_meid_anchor(anchor) -> str:
    """XTTV spielplan: dates live on li.gameSummary; nested result lines also link meid."""
    node = anchor
    for _ in range(12):
        if node is None:
            break
        classes = node.get("class") or []
        if node.name == "li" and "gameSummary" in classes:
            date_span = node.find(class_="spieldatum_datum")
            if date_span:
                return date_span.get_text(" ", strip=True)
            return node.get_text(" ", strip=True)
        if node.name == "tr" and "resultDetailLine" not in classes:
            return node.get_text(" ", strip=True)
        node = node.parent
    if anchor.parent:
        return anchor.parent.get_text(" ", strip=True)
    return ""


def parse_schedule_entries(html: str) -> list[tuple[int, date | None]]:
    """MEIDs from schedule/spielplan rows with best-effort match date from row text."""
    soup = BeautifulSoup(html, "html.parser")
    entries: list[tuple[int, date | None]] = []
    seen: set[int] = set()
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if "meid" not in href.lower():
            continue
        params = parse_qs(urlparse(urljoin(BASE, href)).query)
        meid_raw = params.get("meid", [None])[0]
        if not meid_raw or not str(meid_raw).isdigit():
            continue
        meid = int(meid_raw)
        if meid in seen:
            continue
        seen.add(meid)
        context = _schedule_block_for_meid_anchor(anchor)
        entries.append((meid, _parse_first_date(context)))
    return entries


def parse_meids_from_html(html: str) -> list[int]:
    """Extract unique MEIDs from any XTTV HTML (schedule or index)."""
    return [meid for meid, _ in parse_schedule_entries(html)]


def schedule_import_candidates(
    missing: list[int],
    meid_dates: dict[int, date],
    *,
    today: date | None = None,
) -> list[int]:
    """MEIDs worth importing: played matches (incl. today) and undated schedule links.

    Future-dated fixtures stay on the XTTV plan but have no Spielbericht yet — skip them.
    """
    ref = today or date.today()
    out: list[int] = []
    for meid in missing:
        meid = int(meid)
        match_day = meid_dates.get(meid)
        if match_day is None or match_day <= ref:
            out.append(meid)
    return out


def prioritize_missing_meids(
    missing: list[int],
    meid_dates: dict[int, date],
    *,
    today: date | None = None,
) -> list[int]:
    """Newest played match first; undated MEIDs after that (higher MEID first)."""
    ref = today or date.today()
    candidates = schedule_import_candidates(missing, meid_dates, today=ref)

    def sort_key(meid: int) -> tuple:
        match_day = meid_dates.get(meid)
        if match_day is None:
            return (1, -meid)
        return (0, -match_day.toordinal(), meid)

    return sorted({int(m) for m in candidates}, key=sort_key)


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


def schedule_entries_for_lid(lid: int, sjid: int, *, limit: int = 500) -> list[tuple[int, date | None]]:
    url = (
        f"{BASE}index.php?oid=191&sjid={sjid}&do=spiele&showMenu=Y&lid={lid}"
        f"&zeit=all&limit={limit}"
    )
    html = _fetch_html(url)
    return parse_schedule_entries(html)


def schedule_meids_for_lid(lid: int, sjid: int, *, limit: int = 500) -> list[int]:
    return [meid for meid, _ in schedule_entries_for_lid(lid, sjid, limit=limit)]


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
    meid_dates: dict[int, date] = {}
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
                entries = schedule_entries_for_lid(lid, sjid)
                for meid, match_day in entries:
                    season_meids.add(meid)
                    if match_day is not None and (
                        meid not in meid_dates or match_day > meid_dates[meid]
                    ):
                        meid_dates[meid] = match_day
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
        "meid_dates": meid_dates,
        "by_season": by_season,
    }


# Weekly refresh: import every eligible schedule MEID (no 200 cap). Manual/UI may still pass a limit.
_SCHEDULE_IMPORT_UNLIMITED = 1_000_000


def _schedule_batch_size() -> int:
    """0 / unset = no per-round cap (import all eligible missing MEIDs)."""
    raw = os.environ.get("DATA_REFRESH_BATCH_SIZE", "0").strip().lower()
    if raw in {"", "0", "all", "unlimited", "none"}:
        return _SCHEDULE_IMPORT_UNLIMITED
    if raw.isdigit():
        return max(int(raw), 1)
    return _SCHEDULE_IMPORT_UNLIMITED


def _schedule_max_rounds() -> int:
    """0 / unset = keep draining until no eligible backlog or no progress."""
    raw = os.environ.get("DATA_REFRESH_SCHEDULE_MAX_ROUNDS", "0").strip().lower()
    if raw in {"", "0", "all", "unlimited", "none"}:
        return 10_000
    return max(1, int(raw)) if raw.isdigit() else 10_000


def count_missing_past_meids(
    missing: list[int],
    meid_dates: dict[int, date],
    *,
    today: date | None = None,
) -> int:
    ref = today or date.today()
    return sum(
        1 for meid in missing
        if (d := meid_dates.get(meid)) is not None and d <= ref
    )


def import_meid_batch(
    missing: list[int],
    *,
    limit: int,
) -> dict:
    """Try to import MEIDs from ``missing`` (newest played first). ``limit`` may be large for weekly runs."""
    limit = max(int(limit), 0) or _SCHEDULE_IMPORT_UNLIMITED
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
    discovery: dict | None = None,
) -> dict:
    """Import one batch of schedule MEIDs not yet in the DB (date-prioritized)."""
    create_all()
    discovery = discovery or collect_schedule_meids(sjids=sjids_for_scope(schedule_scope))
    meid_dates = discovery.get("meid_dates") or {}
    missing_all = [m for m in discovery["meids"] if not _is_imported(m)]
    missing = prioritize_missing_meids(missing_all, meid_dates)
    batch = import_meid_batch(missing, limit=limit)

    return {
        "ok": True,
        "mode": "schedule",
        "schedule_scope": schedule_scope,
        "discovery": {
            "leagues_checked": discovery["leagues_checked"],
            "meids_on_schedules": discovery["meids_total"],
            "missing_in_db": len(missing),
            "missing_past": count_missing_past_meids(missing, meid_dates),
            "by_season": discovery["by_season"],
            "sjids": discovery["sjids"],
        },
        **batch,
    }


def drain_schedule_backlog(
    *,
    schedule_scope: str = "all",
    batch_size: int | None = None,
    max_rounds: int | None = None,
) -> dict:
    """Repeat date-prioritized schedule imports until backlog cleared or no progress."""
    create_all()
    batch_size = batch_size if batch_size is not None else _schedule_batch_size()
    max_rounds = max_rounds if max_rounds is not None else _schedule_max_rounds()
    print(
        f"[xttv-schedule] Index/Spielpläne laden (scope={schedule_scope}) …",
        file=sys.stderr,
        flush=True,
    )
    discovery = collect_schedule_meids(sjids=sjids_for_scope(schedule_scope))
    meid_dates: dict[int, date] = discovery.get("meid_dates") or {}
    missing_all = [m for m in discovery["meids"] if not _is_imported(m)]
    missing_past_start = count_missing_past_meids(missing_all, meid_dates)
    missing_future = len(missing_all) - len(schedule_import_candidates(missing_all, meid_dates))
    print(
        f"[xttv-schedule] {discovery['meids_total']} MEIDs auf Plänen, "
        f"{len(missing_all)} fehlen ({missing_past_start} vergangen, "
        f"{missing_future} zukünftig ohne Bericht) — Import …",
        file=sys.stderr,
        flush=True,
    )
    total_imported: list[int] = []
    rounds = 0
    total_errors = 0
    total_checked = 0
    last_batch: dict = {}
    missing = list(missing_all)

    while rounds < max_rounds:
        eligible = schedule_import_candidates(missing, meid_dates)
        if not eligible:
            break
        rounds += 1
        ordered = prioritize_missing_meids(eligible, meid_dates)
        print(
            f"[xttv-schedule] Runde {rounds}: {len(ordered)} importierbare MEIDs …",
            file=sys.stderr,
            flush=True,
        )
        batch = import_meid_batch(ordered, limit=batch_size)
        last_batch = batch
        total_errors += int(batch.get("errors") or 0)
        total_checked += int(batch.get("checked") or 0)
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
        missing = [
            m for m in missing
            if m not in imported_set and not _is_imported(m)
        ]

    missing_past_remaining = count_missing_past_meids(missing, meid_dates)
    return {
        "ok": True,
        "mode": "schedule_drain",
        "schedule_scope": schedule_scope,
        "rounds": rounds,
        "imported": len(total_imported),
        "imported_meids": total_imported,
        "checked": total_checked,
        "errors": total_errors,
        "discovery": {
            "leagues_checked": discovery["leagues_checked"],
            "meids_on_schedules": discovery["meids_total"],
            "missing_remaining": len(missing),
            "missing_future_on_schedule": max(
                0,
                len(missing) - len(schedule_import_candidates(missing, meid_dates)),
            ),
            "missing_past_remaining": missing_past_remaining,
            "missing_past_at_start": missing_past_start,
            "by_season": discovery["by_season"],
            "sjids": discovery["sjids"],
        },
        "last_batch": last_batch,
    }
