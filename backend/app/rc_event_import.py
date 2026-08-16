from datetime import datetime
import re
from .rc_index import fetch_url

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


def import_event(event_id: int):
    url = EVENT_URL.format(event_id=event_id)
    html, status, content_type, _ = fetch_url(url)
    if status != 200:
        return {"ok": False, "event_id": event_id, "status": status, "url": url, "error": "event fetch failed"}
    players = parse_event_summary(html)
    return {
        "ok": True,
        "event_id": event_id,
        "url": url,
        "fetched_at": datetime.utcnow().isoformat(),
        "content_type": content_type,
        "player_count": len(players),
        "players": players,
    }
