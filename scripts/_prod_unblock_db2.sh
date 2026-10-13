#!/usr/bin/env bash
set -eu
cd /opt/tt-aufstellung
C="docker compose -f docker-compose.prod.yml exec -T postgres psql -U tt -d tt_aufstellung"

$C -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid() AND (state = 'idle in transaction' OR wait_event_type = 'Lock');"

sleep 1
$C -c "SET statement_timeout = '10s'; SELECT count(*) AS xttv_players FROM xttv_players;"

docker compose -f docker-compose.prod.yml restart app
sleep 4
curl -sS -m 15 -o /dev/null -w "home: %{http_code} %{time_total}s\n" https://tt-aufstellung.at/
curl -sS -m 45 -o /dev/null -w "teams: %{http_code} %{time_total}s\n" "https://tt-aufstellung.at/api/teams/players?team=Tragwein%2FKamig%202"
