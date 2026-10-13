"""One-off / manual RC catch-up for all mapped players linked to match imports.

Uses the same import path as weekly refresh (_import_rc_for_players) — does not
change Monday logic. Safe to re-run: incremental updates only add newer snapshots.

Examples:
  python scripts/refresh_rc_for_match_players.py --dry-run
  python scripts/refresh_rc_for_match_players.py --chunk 80 --verify 23784
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent / "backend"))

from sqlalchemy import text

from app.analysis_cache import refresh_analysis_cache
from app.analysis_service import clear_analysis_runtime_caches
from app.data_refresh_service import _import_rc_for_players, match_linked_player_external_ids
from app.db import SessionLocal, create_all
from app.xttv_db_import import get_target_seasons


def _latest_snapshot(pass_id: str) -> dict | None:
    with SessionLocal() as db:
        row = db.execute(
            text(
                """
                SELECT s.observed_at::date AS d, s.rc_rating, s.rc_deviation
                FROM player_rating_snapshots s
                JOIN xttv_players p ON p.id = s.player_id
                WHERE p.external_player_id = :pass_id
                  AND s.source = 'ratingscentral'
                ORDER BY s.observed_at DESC
                LIMIT 1
                """
            ),
            {"pass_id": str(pass_id)},
        ).mappings().first()
    return dict(row) if row else None


def _mapped_targets(external_ids: list[str]) -> int:
    with SessionLocal() as db:
        n = db.execute(
            text(
                """
                SELECT count(*) FROM xttv_players
                WHERE external_player_id IN :ids AND rc_player_id IS NOT NULL
                """
            ).bindparams(__import__("sqlalchemy").bindparam("ids", expanding=True)),
            {"ids": external_ids},
        ).scalar()
    return int(n or 0)


def main() -> int:
    parser = argparse.ArgumentParser(description="RC catch-up for match-linked mapped players")
    parser.add_argument("--dry-run", action="store_true", help="only count targets")
    parser.add_argument("--chunk", type=int, default=100, help="players per _import_rc_for_players batch")
    parser.add_argument("--pause", type=float, default=0.0, help="seconds between chunks (RC rate limit)")
    parser.add_argument("--verify", action="append", default=[], help="Pass-Nr. to print before/after")
    parser.add_argument(
        "--skip-cache",
        action="store_true",
        help="do not refresh analysis cache / runtime caches after import",
    )
    args = parser.parse_args()
    create_all()

    seasons = get_target_seasons()
    external_ids = match_linked_player_external_ids(seasons=seasons)
    mapped = _mapped_targets(external_ids) if external_ids else 0

    print(f"Target seasons: {', '.join(sorted(seasons))}")
    print(f"Distinct Pass-Nr. in matches: {len(external_ids)}")
    print(f"Mapped with rc_player_id: {mapped}")

    for pid in args.verify:
        print(f"VERIFY before {pid}: {_latest_snapshot(pid)}")

    if args.dry_run:
        print("Dry-run — no RC fetch.")
        return 0

    if not external_ids:
        print("Nothing to do.")
        return 0

    started = time.monotonic()
    totals = {
        "imported": 0,
        "errors": 0,
        "targets": 0,
        "full_imports": 0,
        "incremental_updates": 0,
        "chunks": 0,
    }
    chunk_size = max(1, int(args.chunk))
    for offset in range(0, len(external_ids), chunk_size):
        chunk = external_ids[offset : offset + chunk_size]
        result = _import_rc_for_players(chunk)
        totals["chunks"] += 1
        for key in ("imported", "errors", "targets", "full_imports", "incremental_updates"):
            totals[key] += int(result.get(key) or 0)
        print(
            f"chunk {totals['chunks']} "
            f"({offset + 1}-{offset + len(chunk)}/{len(external_ids)}): "
            f"imported={result.get('imported')} errors={result.get('errors')}"
        )
        if args.pause > 0 and offset + chunk_size < len(external_ids):
            time.sleep(args.pause)

    elapsed = round(time.monotonic() - started, 1)
    print(json.dumps({"ok": True, "elapsed_seconds": elapsed, **totals}, ensure_ascii=False))

    for pid in args.verify:
        print(f"VERIFY after {pid}: {_latest_snapshot(pid)}")

    if not args.skip_cache and totals["imported"] > 0:
        clear_analysis_runtime_caches()
        cache = refresh_analysis_cache()
        print(f"analysis_cache: {cache}")

    return 1 if totals["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
