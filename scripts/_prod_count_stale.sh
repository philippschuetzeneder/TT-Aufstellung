#!/bin/bash
cd /opt/tt-aufstellung
docker compose -f docker-compose.prod.yml exec -T app python -c "
from app.data_refresh_service import external_ids_needing_rc_after_match, match_linked_player_external_ids
from app.xttv_db_import import get_target_seasons
stale = external_ids_needing_rc_after_match()
allp = match_linked_player_external_ids()
print('stale', len(stale))
print('all_match_linked', len(allp))
"
