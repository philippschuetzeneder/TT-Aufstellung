#!/usr/bin/env bash
# Weekly data refresh on production (Docker VPS).
# Intended schedule: Monday 04:00 Europe/Vienna (see deploy/tt-aufstellung-weekly-refresh.cron).
# Summer pause: exits until DATA_REFRESH_NOT_BEFORE (default 2026-10-05) even if cron fires earlier.
# Sends one report e-mail per completed run (SMTP_* / REFRESH_REPORT_EMAIL_* in .env).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

NOT_BEFORE="${DATA_REFRESH_NOT_BEFORE:-2026-10-05}"
TODAY="$(date +%Y-%m-%d)"

if [[ -n "$NOT_BEFORE" && "$TODAY" < "$NOT_BEFORE" ]]; then
  echo "[weekly-data-refresh] skipped — summer pause until $NOT_BEFORE (today: $TODAY)"
  exit 0
fi

COMPOSE=(docker compose -f docker-compose.prod.yml)
LOG_DIR="${ROOT}/logs"
mkdir -p "$LOG_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
LOG_FILE="${LOG_DIR}/weekly-data-refresh-${STAMP}.log"
RESULT_FILE="$(mktemp)"
PAYLOAD_FILE="$(mktemp)"
trap 'rm -f "$RESULT_FILE" "$PAYLOAD_FILE"' EXIT

send_report() {
  PAYLOAD_B64="$(base64 -w0 "$PAYLOAD_FILE")"
  "${COMPOSE[@]}" exec -T -e "PAYLOAD_B64=${PAYLOAD_B64}" app python -c "
import base64, json, os
from app.refresh_report_email import send_shell_failure_report, send_weekly_refresh_report
payload = json.loads(base64.b64decode(os.environ['PAYLOAD_B64']))
if 'result' in payload:
    out = send_weekly_refresh_report(
        payload['result'],
        restart_done=bool(payload.get('restart_done')),
        host_note=str(payload.get('host_note') or ''),
    )
else:
    out = send_shell_failure_report(
        str(payload.get('error') or 'Unbekannter Fehler'),
        log_excerpt=str(payload.get('log_excerpt') or ''),
    )
print(json.dumps(out, ensure_ascii=False))
"
}

RESULT=""
RESTART_DONE=false
FAILED=false
FAIL_MSG=""

{
  echo "[weekly-data-refresh] starting at $(date -Is)"

  if ! RESULT="$("${COMPOSE[@]}" exec -T app python -c "
from app.data_refresh_service import run_data_refresh
import json
print(json.dumps(run_data_refresh(restart_server=False), ensure_ascii=False, default=str))
")"; then
    FAILED=true
    FAIL_MSG="docker compose exec / run_data_refresh fehlgeschlagen"
    echo "[weekly-data-refresh] $FAIL_MSG"
  else
    printf '%s' "$RESULT" > "$RESULT_FILE"
    echo "$RESULT"

    if ! python3 - "$RESULT_FILE" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
raise SystemExit(0 if data.get("ok", True) else 1)
PY
    then
      FAILED=true
      FAIL_MSG="Refresh meldet ok=false"
      echo "[weekly-data-refresh] $FAIL_MSG"
    elif python3 - "$RESULT_FILE" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
raise SystemExit(0 if data.get("data_changed") else 1)
PY
    then
      echo "[weekly-data-refresh] data changed — restarting app"
      "${COMPOSE[@]}" restart app
      RESTART_DONE=true
    else
      echo "[weekly-data-refresh] no data changes — skip restart"
    fi
  fi

  echo "[weekly-data-refresh] done at $(date -Is)"
} | tee "$LOG_FILE"

if [[ "$FAILED" == true ]]; then
  python3 - "$FAIL_MSG" "$LOG_FILE" > "$PAYLOAD_FILE" <<'PY'
import json, sys
from pathlib import Path
error, log_path = sys.argv[1], sys.argv[2]
log = Path(log_path).read_text(encoding="utf-8", errors="replace")
print(json.dumps({"error": error, "log_excerpt": log}, ensure_ascii=False))
PY
else
  python3 - "$RESULT_FILE" "$RESTART_DONE" "$LOG_FILE" > "$PAYLOAD_FILE" <<'PY'
import json, sys
from pathlib import Path
result_path, restart_done, log_path = sys.argv[1:4]
result = json.loads(Path(result_path).read_text(encoding="utf-8"))
print(json.dumps({
    "result": result,
    "restart_done": restart_done.lower() == "true",
    "host_note": f"Log-Datei auf dem Server: {log_path}",
}, ensure_ascii=False))
PY
fi

set +e
MAIL_RESULT="$(send_report)"
MAIL_RC=$?
set -e
echo "[weekly-data-refresh] mail: $MAIL_RESULT"

if [[ "$FAILED" == true ]]; then
  exit 1
fi
exit "$MAIL_RC"
