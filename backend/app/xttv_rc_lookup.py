"""Resolve Ratings Central PlayerID via OÖTTV/XTTV Spielersuche RC-Graph form."""
from __future__ import annotations

import re
import urllib.error
import urllib.request
from urllib.parse import urlencode

from bs4 import BeautifulSoup

BASE = "https://oettv.xttv.at/ed/"
USER_AGENT = "Mozilla/5.0 (compatible; TT-Aufstellung/0.2)"
OID = "191"


def split_display_name(full_name: str) -> tuple[str, str]:
    parts = [part for part in re.split(r"\s+", (full_name or "").strip()) if part]
    if len(parts) >= 2:
        return parts[0], " ".join(parts[1:])
    return full_name.strip(), ""


def _fetch_search_html(
    *,
    pass_id: str | None = None,
    nachname: str | None = None,
    vorname: str | None = None,
) -> tuple[str, str]:
    params = {
        "oid": OID,
        "do": "spielersuche",
        "f_anf": "Y",
        "v_anf": "Y",
        "o_p_id": pass_id or "",
        "p_nachname": nachname or "",
        "p_vorname": vorname or "",
        "p_ak": "",
        "p_geschlecht": "",
        "n_code": "",
        "vid": "",
        "p_inaktiv": "1",
        "v_inaktiv": "1",
        "Spielersuche": "suchen",
    }
    url = f"{BASE}index.php?{urlencode(params)}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Referer": f"{BASE}index.php"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        charset = response.headers.get_content_charset() or "iso-8859-1"
        html = response.read().decode(charset, errors="replace")
    return html, url


def _rc_id_from_row(tr) -> int | None:
    for form in tr.find_all("form"):
        if "HistoryGraph.php" not in form.get("action", ""):
            continue
        for inp in form.find_all("input"):
            if inp.get("name") == "PlayerID" and inp.get("value"):
                return int(inp["value"])
    return None


def lookup_rc_player_id(
    *,
    pass_id: str | None = None,
    name: str | None = None,
) -> dict:
    """Return RC PlayerID from XTTV RC-Graph hidden field when available."""
    pass_id = str(pass_id or "").strip()
    nachname, vorname = split_display_name(name or "")

    try:
        url = ""
        if pass_id:
            html, url = _fetch_search_html(pass_id=pass_id)
            for tr in BeautifulSoup(html, "html.parser").find_all("tr"):
                row_text = " ".join(tr.stripped_strings)
                if pass_id in row_text.split():
                    rc_id = _rc_id_from_row(tr)
                    if rc_id is not None:
                        return {
                            "ok": True,
                            "rc_player_id": rc_id,
                            "method": "pass",
                            "search_url": url,
                            "row_preview": row_text[:120],
                        }

        if name and nachname and vorname:
            html, url = _fetch_search_html(nachname=nachname, vorname=vorname)
            for tr in BeautifulSoup(html, "html.parser").find_all("tr"):
                row_text = " ".join(tr.stripped_strings)
                if nachname in row_text and vorname in row_text:
                    rc_id = _rc_id_from_row(tr)
                    if rc_id is not None:
                        return {
                            "ok": True,
                            "rc_player_id": rc_id,
                            "method": "name",
                            "search_url": url,
                            "row_preview": row_text[:120],
                        }

        return {
            "ok": False,
            "rc_player_id": None,
            "method": "pass" if pass_id else "name",
            "search_url": url if pass_id or name else "",
            "reason": "no RC-Graph form in XTTV result row",
        }
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        return {
            "ok": False,
            "rc_player_id": None,
            "method": "error",
            "reason": f"{type(exc).__name__}: {exc}",
        }
