#!/usr/bin/env bash
# Install weekly refresh cron on the production VPS (run as root).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CRON_SRC="${ROOT}/deploy/tt-aufstellung-weekly-refresh.cron"
CRON_DST="/etc/cron.d/tt-aufstellung-weekly-refresh"

if [[ ! -f "$CRON_SRC" ]]; then
  echo "Missing $CRON_SRC" >&2
  exit 1
fi

chmod +x "${ROOT}/scripts/weekly-data-refresh-prod.sh"
chmod +x "${ROOT}/scripts/restart-prod.sh"
mkdir -p "${ROOT}/logs"

# Always LF on disk — CRLF breaks cron ("bad minute") and disables the whole file.
sed 's/\r$//' "$CRON_SRC" > "$CRON_DST"
chmod 644 "$CRON_DST"

if grep -q $'\r' "$CRON_DST" 2>/dev/null; then
  echo "FAIL: $CRON_DST still contains CRLF" >&2
  exit 1
fi

if ! grep -qE '^0 4 \* \* 1 root ' "$CRON_DST"; then
  echo "FAIL: unexpected schedule line in $CRON_DST" >&2
  exit 1
fi

systemctl reload cron 2>/dev/null || service cron reload
sleep 2

if journalctl -u cron --since "2 min ago" --no-pager 2>/dev/null | grep -qiE "tt-aufstellung-weekly-refresh.*(ERROR|bad minute|Syntax error)"; then
  echo "FAIL: cron rejected $CRON_DST — check journalctl -u cron" >&2
  exit 1
fi

echo "OK: installed $CRON_DST (Monday 04:00 Europe/Vienna, CRON_TZ in file)"
echo "Job command:"
grep -E '^[0-9*]' "$CRON_DST" || true
echo "Cron aggregate log: ${ROOT}/logs/cron-weekly-refresh.log"
echo "Per-run logs: ${ROOT}/logs/weekly-data-refresh-*.log"
