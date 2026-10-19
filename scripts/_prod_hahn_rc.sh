#!/bin/bash
cd /opt/tt-aufstellung
docker compose -f docker-compose.prod.yml exec -T postgres psql -U tt -d tt_aufstellung -c "
SELECT id, external_player_id, name, rc_player_id, club
FROM xttv_players WHERE external_player_id = '23754';
"
docker compose -f docker-compose.prod.yml exec -T postgres psql -U tt -d tt_aufstellung -c "
SELECT observed_at, rc_rating, rc_deviation, imported_at
FROM player_rating_snapshots s
JOIN xttv_players p ON p.id = s.player_id
WHERE p.external_player_id = '23754' AND s.source = 'ratingscentral'
ORDER BY observed_at DESC
LIMIT 10;
"
