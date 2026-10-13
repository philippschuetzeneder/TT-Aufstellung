#!/usr/bin/env bash
set -eu
cd /opt/tt-aufstellung
COMPOSE=(docker compose -f docker-compose.prod.yml)

echo "=== Copy app code into container ==="
"${COMPOSE[@]}" cp backend/app/db.py app:/app/backend/app/db.py
"${COMPOSE[@]}" cp backend/app/rc_import.py app:/app/backend/app/rc_import.py
"${COMPOSE[@]}" cp backend/app/data_refresh_service.py app:/app/backend/app/data_refresh_service.py
"${COMPOSE[@]}" cp scripts/refresh_rc_for_match_players.py app:/app/scripts/refresh_rc_for_match_players.py
"${COMPOSE[@]}" cp backend/test_refresh_rc_match_players.py app:/app/backend/test_refresh_rc_match_players.py
if [ -f scripts/lineup_margin_sensitivity.py ]; then
  "${COMPOSE[@]}" cp scripts/lineup_margin_sensitivity.py app:/app/scripts/lineup_margin_sensitivity.py
fi

echo "=== Tests ==="
"${COMPOSE[@]}" exec -T app python -m pytest backend/test_refresh_rc_match_players.py backend/test_rc_import_incremental.py -q

echo "=== RC catch-up dry-run ==="
"${COMPOSE[@]}" exec -T app python scripts/refresh_rc_for_match_players.py --dry-run --verify 23784

LOG="/opt/tt-aufstellung/logs/rc-catchup-$(date +%Y%m%d-%H%M%S).log"
echo "=== RC catch-up START (log: $LOG) ==="
nohup "${COMPOSE[@]}" exec -T app python scripts/refresh_rc_for_match_players.py \
  --chunk 80 --verify 23784 \
  > "$LOG" 2>&1 &
echo "RC catch-up started (nohup). tail -f $LOG"

echo "=== Lineup analysis (inline, read-only) ==="
"${COMPOSE[@]}" exec -T app python /app/scripts/_run_lineup_analysis_inline.py 2>&1 | tee /opt/tt-aufstellung/logs/lineup-analysis-$(date +%Y%m%d-%H%M%S).log
