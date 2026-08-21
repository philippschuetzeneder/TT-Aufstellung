"""Test fetching RC PlayerID from XTTV Spielersuche RC-Graph form."""
from __future__ import annotations

import urllib.request
from urllib.parse import urlencode

from bs4 import BeautifulSoup

BASE = "https://oettv.xttv.at/ed/"
USER_AGENT = "Mozilla/5.0 (compatible; TT-Aufstellung/0.2)"


def fetch_rc_id_from_xttv(
    *,
    pass_id: str | None = None,
    nachname: str | None = None,
    vorname: str | None = None,
) -> tuple[int | None, str, str]:
    params = {
        "oid": "191",
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
        "Spielersuche": "suchen",
    }
    url = f"{BASE}index.php?{urlencode(params)}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Referer": f"{BASE}index.php"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        html = response.read().decode(response.headers.get_content_charset() or "iso-8859-1", errors="replace")

    soup = BeautifulSoup(html, "html.parser")
    target_pass = str(pass_id or "").strip()

    def rc_from_row(tr) -> int | None:
        for form in tr.find_all("form"):
            if "HistoryGraph.php" not in form.get("action", ""):
                continue
            for inp in form.find_all("input"):
                if inp.get("name") == "PlayerID" and inp.get("value"):
                    return int(inp["value"])
        return None

    for tr in soup.find_all("tr"):
        cells = tr.stripped_strings
        txt = " ".join(cells)
        if target_pass and target_pass in txt.split():
            rc_id = rc_from_row(tr)
            if rc_id is not None:
                return rc_id, url, txt[:100]
        if not target_pass and nachname and vorname and nachname in txt and vorname in txt:
            rc_id = rc_from_row(tr)
            if rc_id is not None:
                return rc_id, url, txt[:100]

    return None, url, "no RC-Graph form in matching row"


def split_name(full_name: str) -> tuple[str, str]:
    parts = full_name.strip().split()
    if len(parts) >= 2:
        return parts[0], " ".join(parts[1:])
    return full_name, ""


def main() -> None:
    tests = [
        ("26441", "Auer Sebastian"),
        ("22515", "Bauer Christoph"),
        ("50495", "Al Khalaf Al Braa"),
    ]
    for pass_id, name in tests:
        nachname, vorname = split_name(name)
        rc_id, url, info = fetch_rc_id_from_xttv(pass_id=pass_id)
        method = "pass"
        if rc_id is None:
            rc_id, url, info = fetch_rc_id_from_xttv(nachname=nachname, vorname=vorname)
            method = "name"
        our_suggestion = {
            "26441": 85087,
            "22515": 76040,
            "50495": 44027,
        }
        suggested = our_suggestion.get(pass_id)
        match = "OK" if rc_id == suggested else f"DIFF (we had RC {suggested})"
        print(f"{pass_id} {name}: RC {rc_id} via {method} | {match} | {info}")


if __name__ == "__main__":
    main()
