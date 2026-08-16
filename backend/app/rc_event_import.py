from datetime import datetime
import re
from .rc_index import fetch_url
from .db import SessionLocal, create_all
from .models import PlayerRatingSnapshot, RawSourceDocument, XttvPlayer

EVENT_URL = "https://www.ratingscentral.com/EventSummary.php?EventID={event_id}"


def parse_event_summary(html: str):
    # RC renders the player table in plain HTML. Keep this parser tolerant of
    # whitespace/zero-width characters and extract the PlayerID from links.
    clean = html.replace("\u200b", "")
    pattern = re.compile(
        r'href=["\'](?:https?://www\.ratingscentral\.com/)?Player\.php\?PlayerID=(\d+)["\'][^>]*>(.*?)</a>',
        re.I | re.S,
    )
    players = []
    seen = set()
    for m in pattern.finditer(clean):
        rc_id = int(m.group(1))
        name = re.sub(r"<[^>]+>", " ", m.group(2))
        name = re.sub(r"\s+", " ", name).strip()
        if rc_id in seen or not name:
            continue
        seen.add(rc_id)
        players.append({"rc_player_id": rc_id, "name": name})
    return players


def _parse_rating(value: str):
    value = (value or "").replace("\u200b", "").replace("−", "-").strip()
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:±|\+/-|\+-)\s*(\d+(?:\.\d+)?)", value)
    return (float(m.group(1)), float(m.group(2))) if m else (None, None)


def _parse_event_rows(html: str):
    clean = html.replace("\u200b", "")
    rows = {}
    # The event table contains RC player ID, name, initial, change and final.
    # Extract rows independently of the exact table formatting.
    row_re = re.compile(r"<tr[^>]*>(.*?)</tr>", re.I | re.S)
    cell_re = re.compile(r"<(?:td|th)[^>]*>(.*?)</(?:td|th)>", re.I | re.S)
    link_re = re.compile(r"Player\.php\?PlayerID=(\d+)", re.I)
    for raw_row in row_re.findall(clean):
        cells = [re.sub(r"<[^>]+>", " ", c) for c in cell_re.findall(raw_row)]
        cells = [re.sub(r"\s+", " ", c).strip() for c in cells]
        link = link_re.search(raw_row)
        if not link or len(cells) < 4:
            continue
        rc_id = int(link.group(1))
        name_match = re.search(r">\s*([^<>]+,\s*[^<>]+)\s*</a>", raw_row, re.I | re.S)
        name = re.sub(r"\s+", " ", name_match.group(1)).strip() if name_match else next((c for c in cells if "," in c), "")
        ratings = [_parse_rating(c) for c in cells]
        valid = [(r, d) for r, d in ratings if r is not None]
        if len(valid) >= 2:
            initial, final = valid[0], valid[-1]
            rows[rc_id] = {"rc_player_id": rc_id, "name": name, "initial_rating": initial[0], "initial_deviation": initial[1], "rc_rating": final[0], "rc_deviation": final[1]}
    return rows


def _parse_event_date(html: str):
    # Prefer an explicit ISO date on the page. If RC changes markup, returning
    # None is safer than inventing an observation date.
    matches = re.findall(r"\b(20\d{2}-\d{2}-\d{2})\b", html)
    return min(matches) if matches else None


def import_event(event_id: int):
    create_all()
    url = EVENT_URL.format(event_id=event_id)
    html, status, content_type, _ = fetch_url(url)
    if status != 200:
        return {"ok": False, "event_id": event_id, "status": status, "url": url, "error": "event fetch failed"}

    players = parse_event_summary(html)
    rows = _parse_event_rows(html)
    event_date = _parse_event_date(html)
    if not event_date:
        return {"ok": False, "event_id": event_id, "url": url, "player_count": len(players), "error": "event date not found; refusing to create undated snapshots"}

    imported = updated = unmatched = 0
    with SessionLocal.begin() as session:
        external_id = f"event:{event_id}"
        raw = session.query(RawSourceDocument).filter_by(source="ratingscentral", external_id=external_id).one_or_none()
        if raw is None:
            raw = RawSourceDocument(source="ratingscentral", external_id=external_id, url=url, content=html, content_type=content_type, http_status=status)
            session.add(raw)
            session.flush()
        else:
            raw.url = url; raw.content = html; raw.content_type = content_type; raw.http_status = status; raw.fetched_at = datetime.utcnow()

        observed_at = datetime.fromisoformat(event_date)
        for rc_id, row in rows.items():
            player = session.query(XttvPlayer).filter_by(rc_player_id=rc_id).one_or_none()
            if player is None:
                unmatched += 1
                continue
            snapshot = session.query(PlayerRatingSnapshot).filter_by(player_id=player.id, observed_at=observed_at, source="ratingscentral").one_or_none()
            if snapshot is None:
                snapshot = PlayerRatingSnapshot(player_id=player.id, observed_at=observed_at, source="ratingscentral")
                session.add(snapshot); imported += 1
            else:
                updated += 1
            snapshot.rc_rating = row["rc_rating"]
            snapshot.rc_deviation = row["rc_deviation"]
            snapshot.source_document_id = raw.id
            snapshot.imported_at = datetime.utcnow()
            if player.rc_player_id is None:
                player.rc_player_id = rc_id

    return {"ok": True, "mode": "import", "event_id": event_id, "url": url, "event_date": event_date, "player_count": len(players), "rating_rows": len(rows), "snapshots_created": imported, "snapshots_updated": updated, "unmatched_rc_players": unmatched}


def import_event_batch(start: int, count: int = 10):
    count = min(max(int(count), 1), 100)
    results = []
    for event_id in range(int(start), int(start) - count, -1):
        try:
            results.append(import_event(event_id))
        except Exception as exc:
            results.append({"ok": False, "event_id": event_id, "error": f"{type(exc).__name__}: {exc}"})
    return {"ok": True, "mode": "batch_import", "start": int(start), "count": count, "results": results, "snapshots_created": sum(r.get("snapshots_created", 0) for r in results), "snapshots_updated": sum(r.get("snapshots_updated", 0) for r in results), "errors": sum(not r.get("ok", False) for r in results)}
