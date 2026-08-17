from datetime import datetime
import re
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from bs4 import BeautifulSoup
from .db import SessionLocal, create_all
from .models import PlayerRatingSnapshot, RawSourceDocument, XttvPlayer

RC_BASE = "https://www.ratingscentral.com"
USER_AGENT = "TT-Aufstellung/0.1 (+public RatingsCentral OÖTTV event importer)"
EVENT_URL = f"{RC_BASE}/EventSummary.php?EventID={{event_id}}"


def fetch_url(url: str):
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    with urlopen(req, timeout=30) as response:
        content = response.read().decode("utf-8", errors="replace")
        return content, response.status, response.headers.get("Content-Type"), response.geturl()


def _clean(value: str) -> str:
    value = (value or "").replace("\u200b", "")
    return re.sub(r"\s+", " ", value).strip()


def _parse_rating(value: str):
    value = _clean(value).replace("−", "-").replace("–", "-")
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:±|\+/-|\+-)\s*(\d+(?:\.\d+)?)", value)
    return (float(m.group(1)), float(m.group(2))) if m else (None, None)


def _parse_event_rows(html: str):
    """Parse RC event rows without assuming a Player.php link exists.

    The debug parser already proved that RC exposes rows as ordinary table
    cells: numeric player ID, name, initial rating, change, final rating.
    The previous importer incorrectly required a PlayerID link inside every
    row and therefore returned rating_rows=0.
    """
    soup = BeautifulSoup(html.replace("\u200b", ""), "html.parser")
    rows = {}
    for tr in soup.find_all("tr"):
        cells = [_clean(" ".join(c.stripped_strings)) for c in tr.find_all(["th", "td"])]
        if len(cells) < 4:
            continue

        rc_id = None
        if re.fullmatch(r"\d+", cells[0]):
            rc_id = int(cells[0])
        else:
            link = tr.find("a", href=re.compile(r"[?&]PlayerID=(\d+)", re.I))
            if link:
                m = re.search(r"[?&]PlayerID=(\d+)", link.get("href", ""), re.I)
                if m:
                    rc_id = int(m.group(1))
        if rc_id is None:
            continue

        name = next((c for c in cells[1:] if "," in c), "")
        if not name:
            continue

        valid = []
        for cell in cells:
            rating, deviation = _parse_rating(cell)
            if rating is not None:
                valid.append((rating, deviation))
        if len(valid) < 2:
            continue

        initial, final = valid[0], valid[-1]
        rows[rc_id] = {
            "rc_player_id": rc_id,
            "name": name,
            "initial_rating": initial[0],
            "initial_deviation": initial[1],
            "rc_rating": final[0],
            "rc_deviation": final[1],
        }
    return rows


def _parse_event_date(html: str):
    patterns = [
        r"\b(20\d{2}-\d{2}-\d{2})\b",
        r"\b(\d{1,2})[./-](\d{1,2})[./-](20\d{2})\b",
        r"\b(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(20\d{2})\b",
    ]
    m = re.search(patterns[0], html)
    if m:
        return m.group(1)
    m = re.search(patterns[1], html, re.I)
    if m:
        d, month, year = map(int, m.groups())
        return f"{year:04d}-{month:02d}-{d:02d}"
    months = {name: i for i, name in enumerate(["January","February","March","April","May","June","July","August","September","October","November","December"], 1)}
    m = re.search(patterns[2], html, re.I)
    if m:
        d, month, year = m.groups()
        return f"{int(year):04d}-{months[month.capitalize()]:02d}-{int(d):02d}"
    return None


def import_event(event_id: int):
    create_all()
    url = EVENT_URL.format(event_id=event_id)
    html, status, content_type, _ = fetch_url(url)
    if status != 200:
        return {"ok": False, "event_id": event_id, "status": status, "url": url, "error": "event fetch failed"}

    rows = _parse_event_rows(html)
    event_date = _parse_event_date(html)
    if not event_date:
        return {"ok": False, "event_id": event_id, "url": url, "rating_rows": len(rows), "error": "event date not found; refusing to create undated snapshots"}

    imported = updated = unmatched = 0
    with SessionLocal.begin() as session:
        external_id = f"event:{event_id}"
        raw = session.query(RawSourceDocument).filter_by(source="ratingscentral", external_id=external_id).one_or_none()
        if raw is None:
            raw = RawSourceDocument(source="ratingscentral", external_id=external_id, url=url, content=html, content_type=content_type, http_status=status)
            session.add(raw)
            session.flush()
        else:
            raw.url = url
            raw.content = html
            raw.content_type = content_type
            raw.http_status = status
            raw.fetched_at = datetime.utcnow()

        observed_at = datetime.fromisoformat(event_date)
        for rc_id, row in rows.items():
            player = session.query(XttvPlayer).filter_by(rc_player_id=rc_id).one_or_none()
            if player is None:
                unmatched += 1
                continue
            snapshot = session.query(PlayerRatingSnapshot).filter_by(player_id=player.id, observed_at=observed_at, source="ratingscentral").one_or_none()
            if snapshot is None:
                snapshot = PlayerRatingSnapshot(player_id=player.id, observed_at=observed_at, source="ratingscentral")
                session.add(snapshot)
                imported += 1
            else:
                updated += 1
            snapshot.rc_rating = row["rc_rating"]
            snapshot.rc_deviation = row["rc_deviation"]
            snapshot.source_document_id = raw.id
            snapshot.imported_at = datetime.utcnow()

    return {"ok": True, "mode": "import", "event_id": event_id, "url": url, "event_date": event_date, "rating_rows": len(rows), "snapshots_created": imported, "snapshots_updated": updated, "unmatched_rc_players": unmatched}


def import_event_batch(start: int, count: int = 10):
    count = min(max(int(count), 1), 100)
    results = []
    for event_id in range(int(start), int(start) - count, -1):
        try:
            results.append(import_event(event_id))
        except Exception as exc:
            results.append({"ok": False, "event_id": event_id, "error": f"{type(exc).__name__}: {exc}"})
    return {"ok": True, "mode": "batch_import", "start": int(start), "count": count, "results": results, "snapshots_created": sum(r.get("snapshots_created", 0) for r in results), "snapshots_updated": sum(r.get("snapshots_updated", 0) for r in results), "errors": sum(not r.get("ok", False) for r in results)}
