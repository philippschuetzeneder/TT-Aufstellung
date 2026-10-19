#!/bin/bash
set -euo pipefail
cd /opt/tt-aufstellung
set -a
# shellcheck disable=SC1091
source ./.env
set +a
unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy
export DATABASE_URL="postgresql+psycopg://tt:${POSTGRES_PASSWORD}@172.19.0.2:5432/tt_aufstellung"
export RC_FETCH_TIMEOUT="${RC_FETCH_TIMEOUT:-30}"
LOG="logs/rc-stale-catchup-$(date +%Y%m%d-%H%M%S).log"
echo "RC stale catch-up started $(date -Is)" | tee "$LOG"
docker compose -f docker-compose.prod.yml build app
docker compose -f docker-compose.prod.yml up -d app
docker run --rm --network host \
  -e DATABASE_URL -e RC_FETCH_TIMEOUT \
  tt-aufstellung-app \
  python -u scripts/refresh_rc_for_match_players.py --chunk 80 --verify 23754 --verify 23784 \
  >> "$LOG" 2>&1
echo "refreshing analysis cache..." | tee -a "$LOG"
docker compose -f docker-compose.prod.yml exec -T app python -c "
from app.analysis_cache import refresh_analysis_cache
from app.analysis_service import clear_analysis_runtime_caches
clear_analysis_runtime_caches()
print(refresh_analysis_cache())
" >> "$LOG" 2>&1
docker compose -f docker-compose.prod.yml restart app >> "$LOG" 2>&1
echo "RC stale catch-up finished $(date -Is)" | tee -a "$LOG"
