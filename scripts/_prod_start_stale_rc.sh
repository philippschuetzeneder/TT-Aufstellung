#!/bin/bash
set -euo pipefail
cd /opt/tt-aufstellung
LOG=logs/rc-stale-after-match.log
pkill -f 'refresh_rc_for_match_players' 2>/dev/null || true
sleep 1
: > "$LOG"
export RC_IMPORT_WORKERS="${RC_IMPORT_WORKERS:-1}"
export RC_FETCH_TIMEOUT="${RC_FETCH_TIMEOUT:-45}"
export RC_FETCH_RETRIES="${RC_FETCH_RETRIES:-3}"
echo "RC stale-only started $(date -Is) workers=$RC_IMPORT_WORKERS timeout=$RC_FETCH_TIMEOUT retries=$RC_FETCH_RETRIES" | tee -a "$LOG"
docker compose -f docker-compose.prod.yml exec -T \
  -e RC_IMPORT_WORKERS -e RC_FETCH_TIMEOUT -e RC_FETCH_RETRIES \
  app python -u scripts/refresh_rc_for_match_players.py \
  --stale-only --chunk 30 --pause 0.5 --verify 23754 --verify 23784 \
  >> "$LOG" 2>&1
echo "refreshing analysis cache..." | tee -a "$LOG"
docker compose -f docker-compose.prod.yml exec -T app python -c "
from app.analysis_cache import refresh_analysis_cache
from app.analysis_service import clear_analysis_runtime_caches
clear_analysis_runtime_caches()
print(refresh_analysis_cache())
" >> "$LOG" 2>&1
docker compose -f docker-compose.prod.yml restart app >> "$LOG" 2>&1
echo "RC stale-only finished $(date -Is)" | tee -a "$LOG"
