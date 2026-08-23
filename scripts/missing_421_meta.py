import re
from app.xttv_import import fetch_match

for meid in [437859, 437877, 437880, 437916, 437928]:
    html, _, _, _ = fetch_match(meid)
    title = re.search(r"<title>([^<]+)</title>", html, re.I)
    date = re.search(r"\d{2}\.\d{2}\.\d{4}", html)
    home = re.search(r"Heim-Mannschaft:\s*([^\n<]+)", html)
    away = re.search(r"Gast-Mannschaft:\s*([^\n<]+)", html)
    away = re.search(r"Gast-Mannschaft:\s*([^\n<]+)", html)
    print(meid, date.group(0) if date else "?")
    print(" ", title.group(1).strip() if title else "?")
    print(" ", home.group(1).strip() if home else "?")
    print(" ", away.group(1).strip() if away else "?")
