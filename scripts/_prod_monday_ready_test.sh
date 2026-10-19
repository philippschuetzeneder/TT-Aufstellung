#!/bin/bash
set -euo pipefail
cd /opt/tt-aufstellung
FAIL=0
check() { echo ">> $*"; if "$@"; then echo OK; else echo FAIL; FAIL=1; fi; echo; }

check docker compose -f docker-compose.prod.yml ps app
check curl -sf -o /dev/null -w "site=%{http_code}\n" https://tt-aufstellung.at/
check docker compose -f docker-compose.prod.yml exec -T app python scripts/rc_connectivity_check.py
check docker compose -f docker-compose.prod.yml exec -T app python scripts/refresh_rc_for_match_players.py --stale-only --dry-run
check grep -q stale_after_match backend/app/data_refresh_service.py
check test -f /etc/cron.d/tt-aufstellung-weekly-refresh
check test -f /etc/cron.d/tt-aufstellung-sunday-rc-preflight
check grep -q 'json_line' scripts/weekly-data-refresh-prod.sh

echo "monday_ready exit=$FAIL"
exit "$FAIL"
