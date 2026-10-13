#!/bin/bash
set -euo pipefail
cd /opt/tt-aufstellung
LOG="logs/rc-catchup-latest.log"
: > "$LOG"
echo "RC catch-up started $(date -Is)" | tee -a "$LOG"
docker compose -f docker-compose.prod.yml exec -T app python -u scripts/refresh_rc_for_match_players.py --chunk 80 --verify 23784 >> "$LOG" 2>&1
echo "RC catch-up finished $(date -Is)" | tee -a "$LOG"
