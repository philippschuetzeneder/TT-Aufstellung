#!/usr/bin/env bash
# Sunday RC / Monday readiness check on production (Docker VPS).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

LOG_DIR="${ROOT}/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="${LOG_DIR}/sunday-rc-preflight-$(date +%Y%m%d-%H%M%S).log"

{
  echo "[sunday-rc-preflight] starting at $(date -Is)"
  docker compose -f docker-compose.prod.yml exec -T app python -u scripts/sunday_rc_preflight.py
  echo "[sunday-rc-preflight] done at $(date -Is)"
} | tee "$LOG_FILE"
