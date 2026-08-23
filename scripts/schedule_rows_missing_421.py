"""Schedule rows for missing MEIDs."""
import re
import urllib.request
from urllib.parse import urljoin, urlparse, parse_qs

from bs4 import BeautifulSoup

SCHEDULE_URL = (
    "https://oettv.xttv.at/ed/index.php?"
    "oid=191&sjid=25&order=0&hideColumns=&do=spiele&showMenu=Y&bwid=0&"
    "lid=8278&sprdg=0&sprnr=0&vid=0&tid=0&zeit=all&limit=100"
)
MISSING = {437859, 437877, 437880, 437916, 437928}


def fetch_html(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://oettv.xttv.at/ed/"})
    with urllib.request.urlopen(req, timeout=60) as response:
        return response.read().decode(response.headers.get_content_charset() or "iso-8859-1", errors="replace")


html = fetch_html(SCHEDULE_URL)
soup = BeautifulSoup(html, "html.parser")
for tr in soup.find_all("tr"):
    row_text = " ".join(tr.stripped_strings)
    for a in tr.find_all("a", href=True):
        if "meid" not in a["href"]:
            continue
        params = parse_qs(urlparse(urljoin("https://oettv.xttv.at/ed/", a["href"])).query)
        meid = int(params.get("meid", ["0"])[0])
        if meid in MISSING:
            print(f"meid={meid}  {row_text[:200]}")
