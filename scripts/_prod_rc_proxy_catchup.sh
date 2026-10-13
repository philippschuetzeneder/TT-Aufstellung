#!/bin/bash
set -euo pipefail
cd /opt/tt-aufstellung
set -a
# shellcheck disable=SC1091
source ./.env
set +a
export HTTPS_PROXY=http://127.0.0.1:8888
export HTTP_PROXY=http://127.0.0.1:8888
export DATABASE_URL="postgresql+psycopg://tt:${POSTGRES_PASSWORD}@172.19.0.2:5432/tt_aufstellung"
LOG="logs/rc-catchup-latest.log"
: > "$LOG"
echo "RC catch-up started $(date -Is) via host-network proxy" | tee -a "$LOG"
docker run --rm --network host \
  -e HTTPS_PROXY -e HTTP_PROXY -e DATABASE_URL -e RC_FETCH_TIMEOUT=25 \
  tt-aufstellung-app \
  python -u scripts/refresh_rc_for_match_players.py --chunk 80 --verify 23784 >> "$LOG" 2>&1
echo "RC catch-up finished $(date -Is)" | tee -a "$LOG"
