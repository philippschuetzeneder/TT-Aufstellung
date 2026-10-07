"""Map and import RC history for all players with a PassNr in one league group."""
from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, "backend"))

from sqlalchemy import text

from app.analytics_service import _LEAGUE_STATS_CACHE
from app.db import SessionLocal, create_all
from app.player_analysis_service import resolve_latest_league_season
from app.rc_import import import_rc_player
from app.rc_matching import apply_matches


def league_player_ids(league_group: str) -> list[str]:
    create_all()
    with SessionLocal() as db:
        resolved = resolve_latest_league_season(db, league_group)
        if not resolved:
            raise SystemExit(f"Liga {league_group!r} nicht in der DB (keine Saisonzeile).")
        rows = db.execute(
            text(
                """
                SELECT DISTINCT mp.external_player_id::text AS pid
                FROM match_players mp
                JOIN xttv_matches m ON m.id = mp.match_id
                WHERE m.league = :league
                  AND mp.external_player_id IS NOT NULL
                ORDER BY pid
                """
            ),
            {"league": resolved},
        ).scalars().all()
    return [str(pid) for pid in rows if pid]


def import_mapped(ids: list[str]) -> dict:
    imported = errors = skipped = 0
    details: list[dict] = []
    with SessionLocal() as db:
        from app.models import XttvPlayer

        players = (
            db.query(XttvPlayer)
            .filter(XttvPlayer.external_player_id.in_(ids))
            .all()
        )
    by_id = {str(p.external_player_id): p for p in players}
    for pid in ids:
        player = by_id.get(pid)
        if not player or player.rc_player_id is None:
            skipped += 1
            continue
        try:
            result = import_rc_player(
                int(player.rc_player_id),
                xttv_external_player_id=pid,
                xttv_name=player.name,
                xttv_club=player.club,
            )
            imported += 1
            details.append({
                "pass": pid,
                "name": player.name,
                "rc_player_id": player.rc_player_id,
                "snapshots": result.get("snapshots_upserted"),
            })
        except Exception as exc:
            errors += 1
            details.append({"pass": pid, "name": player.name if player else None, "error": str(exc)})
    return {"imported": imported, "errors": errors, "skipped": skipped, "details": details}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("league", nargs="?", default="421", help="Liga-Gruppe, z. B. 421")
    parser.add_argument("--match-batch", type=int, default=500, help="RC-Matching batch size")
    args = parser.parse_args()

    ids = league_player_ids(args.league)
    print(f"Liga {args.league}: {len(ids)} Spieler (PassNr) in aktueller Saisonzeile")

    match = apply_matches(limit=args.match_batch, offset=0, import_history=True, only_unmapped=True)
    print(f"RC-Matching: applied={match.get('applied')}, ambiguous={match.get('ambiguous')}, not_found={match.get('not_found')}")

    history = import_mapped(ids)
    _LEAGUE_STATS_CACHE.clear()
    print(f"RC-Historie: imported={history['imported']}, skipped={history['skipped']}, errors={history['errors']}")
    for row in history["details"]:
        if "error" in row:
            print("  ERR", row)
        elif history["imported"] <= 15:
            print("  OK ", row["name"], row["pass"], "RC", row["rc_player_id"], "snapshots", row.get("snapshots"))


if __name__ == "__main__":
    main()
