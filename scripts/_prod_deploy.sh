#!/bin/bash
set -euo pipefail
cd /opt/tt-aufstellung
echo "=== git pull ==="
git pull --ff-only origin main
echo "HEAD=$(git rev-parse --short HEAD) $(git log -1 --oneline)"
echo "=== fix CRLF on shell scripts ==="
find scripts -maxdepth 1 -name '*.sh' -exec sed -i 's/\r$//' {} +
chmod +x scripts/weekly-data-refresh-prod.sh scripts/restart-prod.sh 2>/dev/null || true
chmod +x scripts/_prod_rc_direct_catchup.sh scripts/_prod_endcheck.sh 2>/dev/null || true
echo "=== docker build & up ==="
docker compose -f docker-compose.prod.yml up -d --build
echo "=== status ==="
docker compose -f docker-compose.prod.yml ps
echo "=== smoke: rc_import ==="
docker compose -f docker-compose.prod.yml exec -T app python -c "from app.rc_import import parse_player_history; print('rc_import_ok')"
echo "=== smoke: API inside caddy ==="
curl -s -o /dev/null -w "home_http=%{http_code}\n" -H "Host: tt-aufstellung.at" http://127.0.0.1/
echo "=== done ==="
