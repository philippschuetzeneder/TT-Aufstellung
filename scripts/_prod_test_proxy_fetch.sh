#!/bin/bash
set -euo pipefail
cd /opt/tt-aufstellung
set -a
# shellcheck disable=SC1091
source ./.env
set +a
docker run --rm --network host \
  -e HTTPS_PROXY=http://127.0.0.1:8888 \
  -e HTTP_PROXY=http://127.0.0.1:8888 \
  -e DATABASE_URL="postgresql+psycopg://tt:${POSTGRES_PASSWORD}@172.19.0.2:5432/tt_aufstellung" \
  tt-aufstellung-app \
  python /app/scripts/_test_rc_fetch.py
