"""
Full RC history backfill for all mapped XTTV players (local/test DB).

Phase 1: Rebuild snapshots from cached PlayerHistory HTML (no network).
Phase 2: Fetch missing histories from ratingscentral.com (slow, resumable).

Verify with: python scripts/audit_rc_snapshots.py --global
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, "backend"))

from sqlalchemy import text

from app.analytics_service import _LEAGUE_STATS_CACHE
from app.db import SessionLocal, create_all
from app.models import XttvPlayer
from app.rc_import import import_rc_player, rehydrate_rc_player_history

PROGRESS_FILE = Path(ROOT) / "data" / "rc_backfill_progress.json"
STATUS_FILE = Path(ROOT) / "data" / "rc_backfill_status.json"
MIN_SNAPSHOTS = 2
PAUSE_SEC = float(os.environ.get("RC_BACKFILL_PAUSE", "3"))
MAX_ATTEMPTS = int(os.environ.get("RC_BACKFILL_ATTEMPTS", "5"))
os.environ.setdefault("RC_FETCH_TIMEOUT", "45")


def audit() -> dict:
    with SessionLocal() as db:
        return dict(db.execute(text("""
            SELECT
                count(*) FILTER (WHERE rc_player_id IS NOT NULL) AS mapped,
                count(*) FILTER (WHERE snap_count >= :min) AS trend_ready,
                count(*) FILTER (WHERE snap_count = 1) AS one_snap,
                count(*) FILTER (WHERE snap_count = 0 AND rc_player_id IS NOT NULL) AS zero_snap
            FROM (
                SELECT xp.rc_player_id,
                       (SELECT count(*) FROM player_rating_snapshots prs
                        WHERE prs.player_id = xp.id AND prs.source = 'ratingscentral') AS snap_count
                FROM xttv_players xp
            ) t
        """), {"min": MIN_SNAPSHOTS}).mappings().one())


def players_needing_history() -> list[tuple[str, int, str, str | None, int]]:
    with SessionLocal() as db:
        rows = db.execute(text("""
            SELECT xp.external_player_id::text AS pass_id,
                   xp.rc_player_id,
                   xp.name,
                   xp.club,
                   (SELECT count(*) FROM player_rating_snapshots prs
                    WHERE prs.player_id = xp.id AND prs.source = 'ratingscentral') AS snaps
            FROM xttv_players xp
            WHERE xp.rc_player_id IS NOT NULL
            ORDER BY snaps ASC, xp.name
        """)).mappings().all()
    return [
        (r["pass_id"], int(r["rc_player_id"]), r["name"], r["club"], int(r["snaps"] or 0))
        for r in rows
        if int(r["snaps"] or 0) < MIN_SNAPSHOTS
    ]


def load_progress() -> set[str]:
    if not PROGRESS_FILE.exists():
        return set()
    try:
        data = json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
        return set(data.get("done_pass_ids") or [])
    except json.JSONDecodeError:
        return set()


def save_progress(done: set[str]) -> None:
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS_FILE.write_text(
        json.dumps({"done_pass_ids": sorted(done)}, indent=2),
        encoding="utf-8",
    )


def write_status(**fields) -> None:
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {"updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), **fields}
    STATUS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> None:
    create_all()
    before = audit()
    print("=== Before ===", before, flush=True)

    # Phase 1: rehydrate from cache
    rehydrated = skipped = 0
    with SessionLocal() as db:
        rc_ids = db.execute(text("""
            SELECT DISTINCT xp.external_player_id::text, xp.rc_player_id, xp.name, xp.club
            FROM xttv_players xp
            WHERE xp.rc_player_id IS NOT NULL
        """)).all()
    for pass_id, rc_id, name, club in rc_ids:
        try:
            result = rehydrate_rc_player_history(
                int(rc_id),
                xttv_external_player_id=str(pass_id),
                xttv_name=name,
                xttv_club=club,
            )
            if result:
                rehydrated += 1
            else:
                skipped += 1
        except Exception as exc:
            print(f"rehydrate ERR {name} ({pass_id}): {exc}", flush=True)
    print(f"Phase 1 rehydrate: ok={rehydrated} no_cache={skipped}", flush=True)

    mid = audit()
    print("=== After rehydrate ===", mid, flush=True)

    # Phase 2: network fetch
    todo = players_needing_history()
    done = load_progress()
    print(f"Phase 2 network: {len(todo)} players still < {MIN_SNAPSHOTS} snapshots", flush=True)

    ok = err = 0
    total = len(todo)
    write_status(phase="network", ok=0, err=0, total=total, skipped=len(done), message="starting")
    for index, (pass_id, rc_id, name, club, snaps) in enumerate(todo):
        if pass_id in done:
            continue
        if index > 0:
            time.sleep(PAUSE_SEC)
        print(f"[{index + 1}/{total}] {name} (pass={pass_id}, rc={rc_id}, had={snaps} snaps) …", flush=True)
        write_status(
            phase="network",
            index=index + 1,
            total=total,
            ok=ok,
            err=err,
            current_pass=pass_id,
            current_name=name,
            message="fetching",
        )
        last_exc = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                result = import_rc_player(
                    rc_id,
                    xttv_external_player_id=pass_id,
                    xttv_name=name,
                    xttv_club=club,
                )
                ok += 1
                done.add(pass_id)
                save_progress(done)
                print(f"  OK snapshots={result['snapshots_upserted']}", flush=True)
                write_status(
                    phase="network",
                    index=index + 1,
                    total=total,
                    ok=ok,
                    err=err,
                    last_ok=name,
                    last_snapshots=result["snapshots_upserted"],
                    message="ok",
                )
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                print(f"  retry {attempt}/{MAX_ATTEMPTS}: {exc}", flush=True)
                write_status(
                    phase="network",
                    index=index + 1,
                    total=total,
                    ok=ok,
                    err=err,
                    current_name=name,
                    message=f"retry {attempt}/{MAX_ATTEMPTS}",
                    last_error=str(exc),
                )
                time.sleep(PAUSE_SEC * attempt)
        if last_exc is not None:
            err += 1
            print(f"ERR {name} ({pass_id}) rc={rc_id}: {last_exc}", flush=True)
            write_status(phase="network", index=index + 1, total=total, ok=ok, err=err, message="error")

    save_progress(done)
    _LEAGUE_STATS_CACHE.clear()
    after = audit()
    print("=== After network ===", after, flush=True)
    print(f"Network: ok={ok} err={err} (progress in {PROGRESS_FILE})", flush=True)


if __name__ == "__main__":
    main()
