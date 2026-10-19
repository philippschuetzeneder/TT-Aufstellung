#!/bin/bash
set -euo pipefail
cd /opt/tt-aufstellung
FAIL=0
warn() { echo "WARN: $*"; }
fail() { echo "FAIL: $*"; FAIL=1; }
ok() { echo "OK: $*"; }

echo "=== Git / deploy ==="
HEAD=$(git rev-parse --short HEAD)
echo "HEAD=$HEAD $(git log -1 --oneline)"
git fetch origin main -q 2>/dev/null || true
BEHIND=$(git rev-list --count HEAD..origin/main 2>/dev/null || echo "?")
if [[ "$BEHIND" != "0" && "$BEHIND" != "?" ]]; then
  warn "branch $BEHIND commits behind origin/main"
else
  ok "git up to date with origin/main"
fi

echo "=== Cron ==="
if [[ ! -f /etc/cron.d/tt-aufstellung-weekly-refresh ]]; then
  fail "missing /etc/cron.d/tt-aufstellung-weekly-refresh"
else
  if grep -q $'\r' /etc/cron.d/tt-aufstellung-weekly-refresh 2>/dev/null; then
    fail "cron file has CRLF"
  else
    ok "cron file LF"
  fi
  grep -E '^0 4 \* \* 1' /etc/cron.d/tt-aufstellung-weekly-refresh || fail "bad cron schedule line"
  if journalctl -u cron --since "7 days ago" --no-pager 2>/dev/null | grep -qiE 'tt-aufstellung-weekly-refresh.*(bad minute|Syntax error)'; then
    fail "cron journal shows syntax errors for our job"
  else
    ok "no recent cron syntax errors in journal"
  fi
fi

echo "=== Scripts ==="
for s in scripts/weekly-data-refresh-prod.sh scripts/restart-prod.sh; do
  [[ -x "$s" ]] || fail "$s not executable"
done
if file scripts/weekly-data-refresh-prod.sh | grep -q CRLF; then
  fail "weekly-data-refresh-prod.sh CRLF"
else
  ok "weekly shell script LF"
fi

echo "=== Env ==="
set -a
# shellcheck disable=SC1091
source ./.env
set +a
TODAY=$(date +%Y-%m-%d)
NB="${DATA_REFRESH_NOT_BEFORE:-2026-10-05}"
if [[ -n "$NB" && "$TODAY" < "$NB" ]]; then
  fail "still in summer pause until $NB (today $TODAY)"
else
  ok "refresh not paused (today=$TODAY not_before=$NB)"
fi
[[ -n "${REFRESH_REPORT_EMAIL_TO:-}" ]] && ok "REFRESH_REPORT_EMAIL_TO set" || warn "REFRESH_REPORT_EMAIL_TO missing"
[[ -n "${SMTP_HOST:-}" ]] && ok "SMTP_HOST=${SMTP_HOST}" || warn "SMTP_HOST missing"

echo "=== Docker ==="
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml exec -T postgres pg_isready -U tt -d tt_aufstellung >/dev/null && ok "postgres ready" || fail "postgres not ready"

echo "=== DB locks (idle in transaction) ==="
IDLE=$(docker compose -f docker-compose.prod.yml exec -T postgres psql -U tt -d tt_aufstellung -t -c \
  "SELECT count(*) FROM pg_stat_activity WHERE state='idle in transaction' AND datname='tt_aufstellung';" | tr -d ' ')
if [[ "${IDLE:-0}" -gt 0 ]]; then
  warn "$IDLE idle-in-transaction sessions"
else
  ok "no idle in transaction"
fi

echo "=== Scan frontier sanity ==="
docker compose -f docker-compose.prod.yml exec -T app python -c "
from app.db import SessionLocal
from sqlalchemy import text
from app.xttv_db_import import _resolve_scan_frontier, _get_scan_frontier
with SessionLocal() as s:
    last=s.execute(text('SELECT max(meid::bigint) FROM xttv_matches WHERE meid~\"^[0-9]+$\"')).scalar()
    if last is None:
        last=s.execute(text(\"SELECT max(CAST(meid AS bigint)) FROM xttv_matches WHERE meid ~ '^[0-9]+$'\")).scalar()
    raw=_get_scan_frontier()
    resolved=_resolve_scan_frontier(last)
print('last_meid', last, 'frontier_raw', raw, 'frontier_resolved', resolved)
if last and resolved and resolved > last + 50000:
    raise SystemExit('frontier too far ahead')
" && ok "scan frontier plausible" || fail "scan frontier check failed"

echo "=== RC fetch (app container) ==="
docker compose -f docker-compose.prod.yml exec -T app python -c "
from urllib.request import urlopen, Request
r=urlopen(Request('https://www.ratingscentral.com/PlayerHistory.php?PlayerID=55312', headers={'User-Agent':'test'}), timeout=20)
print('rc_http', r.status)
" && ok "RC HTTPS from app" || fail "RC fetch from app container"

echo "=== Dry-run run_data_refresh (no restart) ==="
OUT=$(docker compose -f docker-compose.prod.yml exec -T app python -c "
from app.data_refresh_service import run_data_refresh
import json
print(json.dumps(run_data_refresh(restart_server=False), ensure_ascii=False, default=str))
" 2>&1) || { fail "run_data_refresh exec failed"; echo "$OUT"; }
if [[ -n "${OUT:-}" ]]; then
  echo "$OUT" | head -c 2000
  echo
  if echo "$OUT" | python3 -c "import json,sys; d=json.load(sys.stdin); sys.exit(0 if d.get('ok',True) else 1)" 2>/dev/null; then
    ok "run_data_refresh returned ok"
  else
    fail "run_data_refresh ok=false"
  fi
fi

echo "=== API smoke ==="
curl -sf -o /dev/null -w "api_http=%{http_code}\n" -H "Host: tt-aufstellung.at" http://127.0.0.1/api/teams/players?team=test || warn "api smoke via caddy failed"

echo "=== Summary ==="
if [[ "$FAIL" -eq 0 ]]; then
  echo "READY for Monday 04:00 Europe/Vienna"
else
  echo "NOT READY — fix FAIL items above"
  exit 1
fi
