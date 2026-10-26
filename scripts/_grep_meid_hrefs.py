import re
from app.xttv_schedule_import import BASE, _fetch_html, SEASON_TO_SJID

LID = 8817
SJID = SEASON_TO_SJID["2026/2027"]
url = f"{BASE}index.php?oid=191&sjid={SJID}&do=spiele&showMenu=Y&lid={LID}&zeit=all&limit=500"
html = _fetch_html(url)
for m in re.finditer(r'href="([^"]*meid=462244[^"]*)"', html, re.I):
    print(m.group(1))
    start = max(0, m.start() - 400)
    print(html[start : m.end() + 200])
    print("---")
