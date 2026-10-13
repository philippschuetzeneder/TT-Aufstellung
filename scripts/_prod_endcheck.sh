#!/bin/bash
set -euo pipefail
cd /opt/tt-aufstellung
echo "=== CRON ==="
tail -1 /etc/cron.d/tt-aufstellung-weekly-refresh
echo "=== CATCHUP (tail) ==="
tail -5 logs/rc-catchup-latest.log
echo "=== APP ==="
docker compose -f docker-compose.prod.yml ps app
echo "=== DB counts ==="
docker compose -f docker-compose.prod.yml exec -T postgres psql -U tt -d tt_aufstellung -t <<'SQL'
SELECT 'matches=' || count(*) FROM xttv_matches;
SELECT 'rc_snapshots=' || count(*) FROM player_rating_snapshots WHERE source='ratingscentral';
SELECT 'players_mapped=' || count(*) FROM xttv_players WHERE rc_player_id IS NOT NULL;
SQL
echo "=== Friedinger + Hahn latest RC ==="
docker compose -f docker-compose.prod.yml exec -T postgres psql -U tt -d tt_aufstellung <<'SQL'
SELECT p.external_player_id, p.name, s.observed_at::date, s.rc_rating
FROM player_rating_snapshots s
JOIN xttv_players p ON p.id = s.player_id
WHERE p.external_player_id IN ('23784','23754')
  AND s.source = 'ratingscentral'
ORDER BY p.external_player_id, s.observed_at DESC
LIMIT 6;
SQL
echo "=== RC fetch from app container ==="
docker compose -f docker-compose.prod.yml exec -T app python /tmp/_test_rc_fetch.py 2>/dev/null || echo "skip rc fetch test (no /tmp/_test_rc_fetch.py)"
echo "=== Weekly script dry-run pause check ==="
TODAY=$(date +%Y-%m-%d)
NB="${DATA_REFRESH_NOT_BEFORE:-2026-10-05}"
if [[ -n "$NB" && "$TODAY" < "$NB" ]]; then echo "WOULD_SKIP pause"; else echo "WOULD_RUN refresh (today=$TODAY not_before=$NB)"; fi
