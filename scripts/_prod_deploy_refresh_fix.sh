#!/bin/bash
set -euo pipefail
cd /opt/tt-aufstellung
sed -i 's/\r$//' scripts/weekly-data-refresh-prod.sh
docker compose -f docker-compose.prod.yml build app
docker compose -f docker-compose.prod.yml up -d app
echo "app restarted, checking site..."
curl -sf -o /dev/null -w "site=%{http_code}\n" https://tt-aufstellung.at/
echo "sending missed success report for 2026-10-19 run..."
docker compose -f docker-compose.prod.yml exec -T app python <<'PY'
import json
from app.refresh_report_email import send_weekly_refresh_report

result = json.loads(
    '{"ok": true, "data_changed": true, "elapsed_seconds": 490.8, '
    '"summary": {"xttv": {"imported": 30, "schedule_imported": 30, "forward_imported": 0, '
    '"checked": 73, "errors": 15, "scan_frontier_after": 469275}, '
    '"rc": {"newly_mapped": 1, "imported": 235, "errors": 0, "targets": 235, '
    '"incremental_updates": 235}, "analysis_cache": {"ok": true}}, '
    '"message": "30 neue Spielberichte importiert (Schedule: 30, Forward-Scan: 0)."}'
)
out = send_weekly_refresh_report(
    result,
    restart_done=True,
    host_note="Nachgezogen: App-Neustart + Fix weekly-data-refresh-prod.sh (JSON-Parse). "
    "Original-Lauf: logs/weekly-data-refresh-20261019-040001.log",
)
print(json.dumps(out, ensure_ascii=False))
PY
