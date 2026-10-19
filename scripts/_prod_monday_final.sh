#!/bin/bash
set -euo pipefail
cd /opt/tt-aufstellung
echo "cron:" && grep "^0 4" /etc/cron.d/tt-aufstellung-weekly-refresh
echo "env:" && grep -E "^(DATA_REFRESH_NOT_BEFORE|RC_FETCH|SMTP_HOST|REFRESH_REPORT)" .env
echo "app:" && docker compose -f docker-compose.prod.yml ps app
docker compose -f docker-compose.prod.yml exec -T app python /tmp/frontier_check.py 2>/dev/null || docker compose -f docker-compose.prod.yml exec -T app python -c "
from app.xttv_db_import import _max_imported_meid, _get_scan_frontier, _resolve_scan_frontier
l=_max_imported_meid()
print('last_meid', l, 'frontier_raw', _get_scan_frontier(), 'resolved', _resolve_scan_frontier(l))
"
echo "meid 462243 imported:" 
docker compose -f docker-compose.prod.yml exec -T postgres psql -U tt -d tt_aufstellung -t -c "SELECT external_id, match_date, home_team, away_team FROM xttv_matches WHERE external_id='462243';"
echo "421 2026/2027 matches count:"
docker compose -f docker-compose.prod.yml exec -T postgres psql -U tt -d tt_aufstellung -t -c "SELECT count(*) FROM xttv_matches WHERE league LIKE '421%' AND season='2026/2027';"
curl -sf -o /dev/null -w "site_https=%{http_code}\n" https://tt-aufstellung.at/
