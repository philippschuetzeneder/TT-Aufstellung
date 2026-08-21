import re
import urllib.request
from bs4 import BeautifulSoup

url = "https://oettv.xttv.at/ed/index.php?lid=8277&do=doppel"
req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
with urllib.request.urlopen(req, timeout=30) as r:
    html = r.read().decode("iso-8859-1", errors="replace")

soup = BeautifulSoup(html, "html.parser")
for tr in soup.find_all("tr"):
    text = " ".join(tr.stripped_strings)
    if re.search(r"\d{4,6}", text) and len(text) < 180:
        print(text)
