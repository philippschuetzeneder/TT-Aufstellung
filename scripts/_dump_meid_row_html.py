from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup

from app.xttv_schedule_import import BASE, _fetch_html, SEASON_TO_SJID

TARGET = 462244
SJID = SEASON_TO_SJID["2026/2027"]
LID = 8817

url = f"{BASE}index.php?oid=191&sjid={SJID}&do=spiele&showMenu=Y&lid={LID}&zeit=all&limit=500"
html = _fetch_html(url)
soup = BeautifulSoup(html, "html.parser")
for a in soup.find_all("a", href=True):
    href = a["href"]
    if "meid" not in href.lower():
        continue
    params = parse_qs(urlparse(urljoin(BASE, href)).query)
    meid = params.get("meid", [None])[0]
    if str(meid) != str(TARGET):
        continue
    row = a.find_parent("tr")
    # Spielplan row is outer table, not nested resultDetailLine
    while row and "resultDetailLine" in (row.get("class") or []):
        row = row.find_parent("tr")
    print("=== tr ===")
    print(row.prettify()[:2500] if row else "no tr")
    prev = row.find_previous_sibling("tr") if row else None
    if prev:
        print("=== prev tr ===")
        print(prev.prettify()[:1500])
