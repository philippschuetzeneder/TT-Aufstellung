#!/bin/bash
set -euo pipefail
cd /opt/tt-aufstellung
set -a
source ./.env
set +a
export DATABASE_URL="postgresql+psycopg://tt:${POSTGRES_PASSWORD}@172.19.0.2:5432/tt_aufstellung"
docker run --rm --network host \
  -e DATABASE_URL -e RC_FETCH_TIMEOUT=25 \
  tt-aufstellung-app \
  python -u scripts/_test_rc_fetch.py
