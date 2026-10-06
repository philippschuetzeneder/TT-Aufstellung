#!/usr/bin/env bash
# Run on VPS: import new-season MEID clusters, then verify XTTV schedules vs DB.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
COMPOSE=(docker compose -f docker-compose.prod.yml)
LOG="${ROOT}/logs/prod-backfill-$(date +%Y%m%d-%H%M%S).log"
mkdir -p "${ROOT}/logs"

run_scan() {
  local start=$1 end=$2
  echo "=== scan ${start}-${end} ===" | tee -a "$LOG"
  "${COMPOSE[@]}" exec -T app python -c "
from app.xttv_db_import import scan_import_reports
import json
print(json.dumps(scan_import_reports(start=${start}, end=${end}, limit=200), ensure_ascii=False))
" | tee -a "$LOG"
}

{
  echo "started $(date -Is)"
  run_scan 460000 465000
  run_scan 448190 452000
  run_scan 452000 456000
  run_scan 456000 460000
  echo "=== status ==="
  "${COMPOSE[@]}" exec -T app python scripts/check_prod_import_status.py
  echo "=== verify schedules (sjid=26) ==="
  "${COMPOSE[@]}" exec -T app python scripts/verify_round1_sjid26.py
  echo "=== verify 421 ==="
  "${COMPOSE[@]}" exec -T app python scripts/verify_round1_sjid26.py "421 "
  echo "done $(date -Is)"
} | tee -a "$LOG"
