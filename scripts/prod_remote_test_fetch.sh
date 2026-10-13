#!/bin/bash
set -euo pipefail
cd /opt/tt-aufstellung
set -a
source ./.env
set +a
export DATABASE_URL="postgresql+psycopg://tt:${POSTGRES_PASSWORD}@172.19.0.2:5432/tt_aufstellung"
docker run --rm --network host \
  -e DATABASE_URL -e RC_FETCH_TIMEOUT=25 \
  tt-aufstellung-app \
  python -u - <<'PY'
import urllib.request
print("getproxies", urllib.request.getproxies())
req = urllib.request.Request(
    "https://www.ratingscentral.com/PlayerHistory.php?PlayerID=55312",
    headers={"User-Agent": "TT-Aufstellung/0.1"},
)
with urllib.request.urlopen(req, timeout=20) as r:
    print("status", r.status, "len", len(r.read()))
PY
