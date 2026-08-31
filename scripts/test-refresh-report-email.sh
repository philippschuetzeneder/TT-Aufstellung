#!/usr/bin/env bash
# Send a test refresh report e-mail using SMTP settings from .env (runs in app container).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
COMPOSE=(docker compose -f docker-compose.prod.yml)
"${COMPOSE[@]}" exec -T app python -c "
from datetime import datetime
from zoneinfo import ZoneInfo
from app.refresh_report_email import send_weekly_refresh_report
sample = {
    'ok': True,
    'data_changed': False,
    'elapsed_seconds': 1.2,
    'message': 'Test-E-Mail — kein echter Import.',
    'summary': {
        'xttv': {'imported': 0, 'checked': 0, 'errors': 0},
        'rc': {'skipped': True, 'reason': 'test'},
        'analysis_cache': {'skipped': True},
    },
}
print(send_weekly_refresh_report(sample, restart_done=False, host_note='Manueller SMTP-Test', when=datetime.now(ZoneInfo('Europe/Vienna'))))
"
