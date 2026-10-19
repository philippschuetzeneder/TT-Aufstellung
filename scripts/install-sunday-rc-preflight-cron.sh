#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CRON_SRC="${ROOT}/deploy/tt-aufstellung-sunday-rc-preflight.cron"
CRON_DST="/etc/cron.d/tt-aufstellung-sunday-rc-preflight"

chmod +x "${ROOT}/scripts/sunday-rc-preflight-prod.sh"
mkdir -p "${ROOT}/logs"
sed 's/\r$//' "$CRON_SRC" > "$CRON_DST"
chmod 644 "$CRON_DST"
systemctl reload cron 2>/dev/null || service cron reload
echo "OK: $CRON_DST (Sunday 18:00 Europe/Vienna)"
grep -E '^[0-9*]' "$CRON_DST" || true
