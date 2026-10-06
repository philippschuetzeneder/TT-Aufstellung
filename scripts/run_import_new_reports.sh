#!/usr/bin/env bash
set -euo pipefail
limit="${1:-80}"
docker compose -f docker-compose.prod.yml exec -T app python -c "
from app.xttv_db_import import import_new_reports
import json
print(json.dumps(import_new_reports(limit=int('${limit}')), default=str))
"
