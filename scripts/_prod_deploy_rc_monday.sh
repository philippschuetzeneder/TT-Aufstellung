#!/bin/bash
set -euo pipefail
cd /opt/tt-aufstellung
touch .env
grep -q '^RC_FETCH_TIMEOUT=' .env || echo RC_FETCH_TIMEOUT=45 >> .env
grep -q '^RC_FETCH_RETRIES=' .env || echo RC_FETCH_RETRIES=3 >> .env
grep -q '^RC_IMPORT_WORKERS=' .env || echo RC_IMPORT_WORKERS=1 >> .env
sed -i 's/^RC_IMPORT_WORKERS=4/RC_IMPORT_WORKERS=1/' .env || true
docker compose -f docker-compose.prod.yml up -d --build app
sed -i 's/\r$//' /tmp/rc_vps_verify.sh
bash /tmp/rc_vps_verify.sh
docker compose -f docker-compose.prod.yml exec -T app python -c "
from app.analysis_cache import refresh_analysis_cache
from app.analysis_service import clear_analysis_runtime_caches
clear_analysis_runtime_caches()
print(refresh_analysis_cache())
"
docker compose -f docker-compose.prod.yml restart app
