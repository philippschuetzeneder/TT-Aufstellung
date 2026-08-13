from __future__ import annotations

import time
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urljoin, urlparse
import re

from bs4 import BeautifulSoup

BASE = "https://oettv.xttv.at/ed/"
MATCH_URL = BASE + "spielbericht.inc.php?meid={meid}"


class XttvFetchError(RuntimeError):
    """Raised when an XTTV report cannot be fetched after retries."""


def fetch_match(meid: int, *, retries: int = 3, timeout: int = 30) -> tuple[str, int, str, str]:
    """Fetch one XTTV report with retries for transient server/rate-limit errors.

    XTTV occasionally returns HTTP 500/502/503/504 or 429 even for valid MEIDs.
    Those responses are retried with a short exponential backoff. Permanent
    errors such as 404 are returned immediately and are therefore cheap during
    the MEID scanner.
    """
    if retries < 0:
        raise ValueError("retries must be >= 0")

    url = MATCH_URL.format(meid=meid)
    last_error: Exception | None = None
    transient_statuses = {429, 500, 502, 503, 504}

    for attempt in range(retries + 1):
        request = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; TT-Aufstellung/0.2)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "de-AT,de;q=0.9,en;q=0.5",
            "Referer": f"{BASE}index.php",
            "Connection": "close",
        })
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read()
                charset = response.headers.get_content_charset() or "iso-8859-1"
                return body.decode(charset, errors="replace"), response.status, response.headers.get_content_type(), url
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in transient_statuses or attempt >= retries:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last_error = exc
            if attempt >= retries:
                raise

        time.sleep(0.5 * (2 ** attempt))

    raise XttvFetchError(f"Unable to fetch XTTV report {meid}: {last_error}")


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def player_id_from_link(link) -> str | None:
    href = link.get("href", "")
    params = parse_qs(urlparse(urljoin(BASE, href)).query)
    for key in ("spid", "playerid", "pid", "passnr", "passnummer"):
        if params.get(key):
            return params[key][0]
    return None


def inspect_html(html: str) -> dict:
    """Return a compact diagnostic view of an XTTV HTML response."""
    soup = BeautifulSoup(html, "html.parser")
    tables = []
    for ti, table in enumerate(soup.find_all("table")):
        rows = []
        for tr in table.find_all("tr"):
            cells = [clean(c.get_text(" ", strip=True)) for c in tr.find_all(["th", "td"])]
            if cells:
                rows.append(cells)
        tables.append({"index": ti, "id": table.get("id"), "class": table.get("class"), "rows": rows})
    links = []
    for link in soup.find_all("a", href=True):
        text = clean(link.get_text(" ", strip=True))
        if text:
            links.append({"text": text, "href": link.get("href"), "player_id": player_id_from_link(link)})
    return {
        "title": clean(soup.title.get_text(" ", strip=True)) if soup.title else None,
        "meta_charset": next((m.get("content") for m in soup.find_all("meta") if "charset" in m.attrs), None),
        "table_count": len(tables),
        "tables": tables,
        "links": links,
        "text": clean(soup.get_text(" ", strip=True)),
    }
