"""One-shot RC backfill status (for watch script)."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.chdir(ROOT)

from sqlalchemy import text

from app.db import SessionLocal

status_path = ROOT / "data" / "rc_backfill_status.json"
if status_path.exists():
    print("status:", json.loads(status_path.read_text(encoding="utf-8")))

with SessionLocal() as db:
    row = db.execute(text("""
        SELECT
          count(*) FILTER (WHERE snap_count >= 2) AS trend_ready,
          count(*) FILTER (WHERE snap_count = 1) AS one_snap,
          count(*) FILTER (WHERE snap_count = 0) AS zero_snap
        FROM (
          SELECT (SELECT count(*) FROM player_rating_snapshots prs
                  WHERE prs.player_id = xp.id AND prs.source = 'ratingscentral') AS snap_count
          FROM xttv_players xp WHERE xp.rc_player_id IS NOT NULL
        ) t
    """)).mappings().one()
print("db:", dict(row))

progress = ROOT / "data" / "rc_backfill_progress.json"
if progress.exists():
    done = len(json.loads(progress.read_text(encoding="utf-8")).get("done_pass_ids") or [])
    print(f"network_done_pass_ids: {done}")
