#!/bin/bash
set -euo pipefail
cd /opt/tt-aufstellung
echo "=== VPS curl RC ==="
curl -sf -o /dev/null -w "rc_http=%{http_code} time=%{time_total}s\n" --max-time 25 \
  "https://www.ratingscentral.com/PlayerHistory.php?PlayerID=55291"
echo "=== app connectivity check ==="
docker compose -f docker-compose.prod.yml exec -T app python scripts/rc_connectivity_check.py
echo "=== stale count dry-run ==="
docker compose -f docker-compose.prod.yml exec -T app python scripts/refresh_rc_for_match_players.py --stale-only --dry-run
echo "=== friedinger incremental ==="
docker compose -f docker-compose.prod.yml exec -T app python <<'PY'
from app.db import SessionLocal
from app.models import XttvPlayer
from app.rc_import import import_rc_player_updates
from sqlalchemy import text

with SessionLocal() as db:
    p = db.query(XttvPlayer).filter_by(external_player_id="23784").one()
    row = db.execute(
        text(
            "SELECT rc_rating, observed_at::date FROM player_rating_snapshots "
            "WHERE player_id=:pid AND source='ratingscentral' ORDER BY observed_at DESC LIMIT 1"
        ),
        {"pid": p.id},
    ).first()
    print("before", row)
    out = import_rc_player_updates(
        int(p.rc_player_id),
        xttv_external_player_id=p.external_player_id,
        xttv_name=p.name,
        xttv_club=p.club,
    )
    print("import", out)
    row2 = db.execute(
        text(
            "SELECT rc_rating, observed_at::date FROM player_rating_snapshots "
            "WHERE player_id=:pid AND source='ratingscentral' ORDER BY observed_at DESC LIMIT 1"
        ),
        {"pid": p.id},
    ).first()
    print("after", row2)
PY
