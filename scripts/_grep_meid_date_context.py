import re
from app.xttv_schedule_import import BASE, _fetch_html, SEASON_TO_SJID

LID = 8817
SJID = SEASON_TO_SJID["2026/2027"]
url = f"{BASE}index.php?oid=191&sjid={SJID}&do=spiele&showMenu=Y&lid={LID}&zeit=all&limit=500"
html = _fetch_html(url)
idx = html.lower().find("meid=462244")
chunk = html[max(0, idx - 1200) : idx + 800]
print(chunk)
print("dates:", re.findall(r"\d{2}\.\d{2}\.\d{4}", chunk))
