#!/bin/bash
LOG="${1:-/opt/tt-aufstellung/logs/rc-stale-after-match.log}"
if [[ ! -f "$LOG" ]]; then
  echo "log_missing=$LOG"
  exit 0
fi
grep '^chunk ' "$LOG" | tail -3
echo "chunks_done=$(grep -c '^chunk ' "$LOG")"
echo "targets=$(grep '^Pass-Nr. to refresh:' "$LOG" | tail -1 | awk '{print $NF}')"
echo "timeouts=$(grep -c 'timed out' "$LOG")"
grep 'VERIFY after\|"ok":\|analysis_cache\|elapsed_seconds' "$LOG" | tail -6
pgrep -f 'refresh_rc_for_match_players' >/dev/null && echo status=running || echo status=finished
curl -sf -o /dev/null -w "site=%{http_code}\n" https://tt-aufstellung.at/ || echo site=check_failed
