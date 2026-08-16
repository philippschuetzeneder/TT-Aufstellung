"""Helpers for building a local Ratings Central index from OÖTTV event summaries."""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

import requests
from bs4 import BeautifulSoup

RC_EVENT_URL = "https://www.ratingscentral.com/EventSummary.php?EventID={event_id}"


def parse_event_summary(html: str) -> list[dict[str, Any]]:
    """Extract RC player rows from an EventSummary page.

    RC renders these pages as HTML tables.  We deliberately parse by column
    headings instead of relying on a fixed table index, because the page has
    changed layout over time.
    """
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict[str, Any]] = []
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        headers = [c.get_text(" ", strip=True).lower() for c in rows[0].find_all(["th", "td"])]
        if not headers or "initial rating" not in " ".join(headers):
            continue
        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            if len(cells) < 2:
                continue
            text = [c.get_text(" ", strip=True) for c in cells]
            links = row.find_all("a", href=True)
            rc_id = None
            name = None
            for a in links:
                m = re.search(r"PlayerID=(\d+)", a.get("href", ""), re.I)
                if m:
                    rc_id = int(m.group(1))
                    name = a.get_text(" ", strip=True)
                    break
            if not rc_id or not name:
                continue
            out.append({"rc_player_id": rc_id, "name": name, "cells": text})
    return out


def fetch_event(event_id: int, timeout: int = 30) -> list[dict[str, Any]]:
    url = RC_EVENT_URL.format(event_id=event_id)
    response = requests.get(url, timeout=timeout, headers={"User-Agent": "TT-Aufstellung/1.0"})
    response.raise_for_status()
    return parse_event_summary(response.text)


def recent_event_ids(end: date, years: int = 3) -> list[int]:
    """Placeholder for the event-discovery layer.

    Discovery is intentionally separate from parsing/fetching: once the
    OÖTTV event list is known, hundreds of summaries can be fetched with a
    bounded worker pool and cached by event ID.
    """
    _ = end - timedelta(days=365 * years)
    return []
