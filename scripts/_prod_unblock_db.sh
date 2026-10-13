#!/usr/bin/env bash
set -eu
cd /opt/tt-aufstellung
C="docker compose -f docker-compose.prod.yml exec -T postgres psql -U tt -d tt_aufstellung -v ON_ERROR_STOP=1"

echo "=== Before ==="
$C -c "SELECT pid, state, wait_event_type, now()-query_start AS age, left(query,100) AS q
FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid()
ORDER BY query_start NULLS LAST;"

echo "=== Terminate idle-in-transaction and long blocked readers (>60s on team_players) ==="
$C -t -A -c "
SELECT pid FROM pg_stat_activity
WHERE datname = current_database()
  AND pid <> pg_backend_pid()
  AND (
    state = 'idle in transaction'
    OR (state = 'active' AND wait_event_type = 'Lock' AND query ILIKE '%team_players%')
    OR (state = 'active' AND query ILIKE '%ALTER TABLE xttv_players%')
  );
" | while read -r pid; do
  [ -z "$pid" ] && continue
  echo "terminating pid $pid"
  $C -c "SELECT pg_terminate_backend($pid);"
done

sleep 2

echo "=== After ==="
$C -c "SELECT pid, state, wait_event_type, now()-query_start AS age, left(query,100) AS q
FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid()
ORDER BY query_start NULLS LAST;"

echo "=== Quick read test ==="
$C -c "SET statement_timeout = '5s'; SELECT count(*) FROM xttv_players;"

echo "=== Restart app (clear stuck HTTP workers) ==="
docker compose -f docker-compose.prod.yml restart app

sleep 3

echo "=== HTTP smoke ==="
curl -sS -m 10 -o /dev/null -w "home: %{http_code} %{time_total}s\n" https://tt-aufstellung.at/ || true
curl -sS -m 30 -o /dev/null -w "teams: %{http_code} %{time_total}s\n" \
  "https://tt-aufstellung.at/api/teams/players?team=Tragwein%2FKamig%202" || true
