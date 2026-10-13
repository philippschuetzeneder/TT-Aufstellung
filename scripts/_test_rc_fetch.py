import os
import urllib.request

proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
print("proxy env:", proxy)
print("getproxies:", urllib.request.getproxies())

req = urllib.request.Request(
    "https://www.ratingscentral.com/PlayerHistory.php?PlayerID=55312",
    headers={"User-Agent": "TT-Aufstellung/0.1"},
)
handlers = []
if proxy:
    handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
opener = urllib.request.build_opener(*handlers)
with opener.open(req, timeout=20) as r:
    data = r.read()
    print("status", r.status, "bytes", len(data))
