#!/bin/bash
set -euo pipefail
cd /opt/tt-aufstellung

echo "=== RC_FETCH_TIMEOUT in .env ==="
if grep -q '^RC_FETCH_TIMEOUT=' .env 2>/dev/null; then
  sed -i 's/^RC_FETCH_TIMEOUT=.*/RC_FETCH_TIMEOUT=30/' .env
else
  echo 'RC_FETCH_TIMEOUT=30' >> .env
fi
grep RC_FETCH_TIMEOUT .env

echo "=== SMTP: prefer host.docker.internal (Postfix on VPS host) ==="
if grep -q '^SMTP_HOST=' .env; then
  sed -i 's/^SMTP_HOST=.*/SMTP_HOST=host.docker.internal/' .env
else
  echo 'SMTP_HOST=host.docker.internal' >> .env
fi
grep -E '^(SMTP_|REFRESH_REPORT)' .env | sed 's/PASSWORD=.*/PASSWORD=***/'

echo "=== Postfix on host ==="
systemctl is-active postfix 2>/dev/null || service postfix status 2>/dev/null | head -3 || echo "postfix status unknown"

echo "=== Recreate app (load .env) ==="
docker compose -f docker-compose.prod.yml up -d --force-recreate app
sleep 4
docker compose -f docker-compose.prod.yml exec -T app env | grep -E 'RC_FETCH|SMTP_HOST|REFRESH_REPORT'

echo "=== RC smoke (single player) ==="
docker compose -f docker-compose.prod.yml exec -T app python -c "
import os
from app.rc_import import fetch_player_history
print('timeout', os.environ.get('RC_FETCH_TIMEOUT'))
h,_=fetch_player_history(55312)
print('rc_bytes', len(h))
"

echo "=== SMTP connect from app ==="
docker compose -f docker-compose.prod.yml exec -T app python -c "
import os, smtplib
h=os.environ.get('SMTP_HOST','')
p=int(os.environ.get('SMTP_PORT','25'))
print('connect', h, p)
with smtplib.SMTP(h, p, timeout=15) as s:
    print('ehlo', s.ehlo()[0] if s.ehlo() else ok)
"

echo "=== Send test report email ==="
bash scripts/test-refresh-report-email.sh

echo "=== Mail queue on host ==="
mailq 2>/dev/null | head -15 || true
journalctl -u postfix --since "5 min ago" --no-pager 2>/dev/null | tail -20 || true

echo "=== API smoke ==="
curl -s -o /dev/null -w "https_home=%{http_code}\n" https://tt-aufstellung.at/ || true
