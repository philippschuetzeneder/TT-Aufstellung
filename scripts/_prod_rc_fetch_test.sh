#!/bin/bash
set -euo pipefail
cd /opt/tt-aufstellung
echo "=== curl from host ==="
curl -sf -o /dev/null -w "host http=%{http_code} time=%{time_total}s\n" --max-time 45 \
  "https://www.ratingscentral.com/PlayerHistory.php?PlayerID=53678" || echo host_curl_failed
echo "=== python from app (55291 Hahn, 53678 sample) ==="
docker compose -f docker-compose.prod.yml exec -T app python <<'PY'
import os, time
from app.rc_import import fetch_player_history

for pid in (53678, 55291):
    t = time.time()
    try:
        html, _ = fetch_player_history(pid)
        print(f"PlayerID={pid} ok bytes={len(html)} sec={time.time()-t:.1f} timeout={os.environ.get('RC_FETCH_TIMEOUT')}")
    except Exception as e:
        print(f"PlayerID={pid} FAIL {type(e).__name__}: {e} sec={time.time()-t:.1f}")
PY
echo "=== run status ==="
bash /tmp/rc_status.sh /opt/tt-aufstellung/logs/rc-stale-after-match.log 2>/dev/null || true
