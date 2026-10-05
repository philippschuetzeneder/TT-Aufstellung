#!/usr/bin/env bash
# Install weekly refresh cron on the production VPS (run as root).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CRON_SRC="${ROOT}/deploy/tt-aufstellung-weekly-refresh.cron"
CRON_DST="/etc/cron.d/tt-aufstellung-weekly-refresh"

chmod +x "${ROOT}/scripts/weekly-data-refresh-prod.sh"
chmod +x "${ROOT}/scripts/restart-prod.sh"

sed 's/\r$//' "$CRON_SRC" > "$CRON_DST"
chmod 644 "$CRON_DST"

echo "Installed $CRON_DST"
echo "Schedule: Monday 04:00 Europe/Vienna (first active run: 2026-10-05)"
crontab -l 2>/dev/null || true
grep -n tt-aufstellung "$CRON_DST" || true
