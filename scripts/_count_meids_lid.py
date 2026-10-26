import re

from app.xttv_schedule_import import BASE, _fetch_html, parse_schedule_entries, SEASON_TO_SJID

LID = 8817
SJID = SEASON_TO_SJID["2026/2027"]
url = f"{BASE}index.php?oid=191&sjid={SJID}&do=spiele&showMenu=Y&lid={LID}&zeit=all&limit=500"
html = _fetch_html(url)
regex_meids = set(int(x) for x in re.findall(r"meid=(\d+)", html, re.I))
parsed = parse_schedule_entries(html)
print("regex unique", len(regex_meids))
print("parse unique", len(parsed))
