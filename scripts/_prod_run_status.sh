#!/bin/bash
cd /opt/tt-aufstellung
docker compose -f docker-compose.prod.yml ps app
curl -sf -o /dev/null -w "site=%{http_code}\n" https://tt-aufstellung.at/
docker compose -f docker-compose.prod.yml exec -T postgres psql -U tt -d tt_aufstellung -t -c "SELECT count(*) AS liga421 FROM xttv_matches WHERE league LIKE '421%' AND season='2026/2027';"
docker compose -f docker-compose.prod.yml exec -T postgres psql -U tt -d tt_aufstellung -t -c "SELECT external_id FROM xttv_matches WHERE external_id IN ('461701','462243') ORDER BY external_id;"
grep "weekly-data-refresh" logs/cron-weekly-refresh.log | tail -3
ls -la logs/weekly-data-refresh-20251019*.log 2>/dev/null | tail -1
grep "Oct 19 04:" /var/log/mail.log 2>/dev/null | tail -15 || true
echo "imported today sample leagues:"
docker compose -f docker-compose.prod.yml exec -T postgres psql -U tt -d tt_aufstellung -c "SELECT league, count(*) AS n FROM xttv_matches WHERE external_id IN ('461701','461700','461613','461069','461073') GROUP BY league;"
echo "421 matches:"
docker compose -f docker-compose.prod.yml exec -T postgres psql -U tt -d tt_aufstellung -c "SELECT external_id, match_date, home_team, away_team FROM xttv_matches WHERE league LIKE '421%' AND season='2026/2027' ORDER BY match_date DESC LIMIT 8;"
ls -lt logs/weekly-data-refresh*.log | head -2
grep -i mail logs/cron-weekly-refresh.log || true
