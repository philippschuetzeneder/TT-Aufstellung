"""Audit RC mapping vs snapshot depth for league or whole DB."""
from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, "backend"))

from sqlalchemy import text

from app.db import SessionLocal, create_all
from app.player_analysis_service import resolve_latest_league_season


def audit_league(league_group: str) -> None:
    create_all()
    with SessionLocal() as db:
        resolved = resolve_latest_league_season(db, league_group)
        if not resolved:
            print(f"Keine Saisonzeile für Liga {league_group!r}")
            return
        print(f"Liga: {resolved}\n")
        rows = db.execute(
            text(
                """
                WITH roster AS (
                    SELECT DISTINCT mp.external_player_id::text AS pass_id, max(mp.name) AS name
                    FROM match_players mp
                    JOIN xttv_matches m ON m.id = mp.match_id
                    WHERE m.league = :league AND mp.external_player_id IS NOT NULL
                    GROUP BY mp.external_player_id
                )
                SELECT
                    r.pass_id,
                    r.name,
                    xp.rc_player_id,
                    count(prs.id) FILTER (WHERE prs.source = 'ratingscentral') AS snapshots
                FROM roster r
                LEFT JOIN xttv_players xp ON xp.external_player_id = r.pass_id
                LEFT JOIN player_rating_snapshots prs ON prs.player_id = xp.id
                GROUP BY r.pass_id, r.name, xp.rc_player_id
                ORDER BY snapshots NULLS FIRST, r.name
                """
            ),
            {"league": resolved},
        ).mappings().all()

    total = len(rows)
    mapped = sum(1 for r in rows if r["rc_player_id"] is not None)
    no_xttv = sum(1 for r in rows if r["snapshots"] is None and r["rc_player_id"] is None)
    zero_snaps = sum(1 for r in rows if r["rc_player_id"] is not None and (r["snapshots"] or 0) == 0)
    one_snap = sum(1 for r in rows if (r["snapshots"] or 0) == 1)
    trend_ok = sum(1 for r in rows if (r["snapshots"] or 0) >= 2)
    print(f"Spieler in Saison-Kader: {total}")
    print(f"  RC gemappt (rc_player_id): {mapped}")
    print(f"  >=2 Snapshots (Trend moeglich): {trend_ok}")
    print(f"  genau 1 Snapshot: {one_snap}")
    print(f"  gemappt aber 0 Snapshots: {zero_snaps}")
    print(f"  ohne xttv_players/RC: {no_xttv - zero_snaps if zero_snaps else no_xttv}")
    print()
    weak = [r for r in rows if (r["snapshots"] or 0) < 2]
    print(f"Ohne ausreichend Historie (<2 Snapshots): {len(weak)}")
    for r in weak[:25]:
        print(f"  {r['name']}: pass={r['pass_id']} rc={r['rc_player_id']} snaps={r['snapshots'] or 0}")
    if len(weak) > 25:
        print(f"  … und {len(weak) - 25} weitere")


def audit_global() -> None:
    create_all()
    with SessionLocal() as db:
        row = db.execute(
            text(
                """
                SELECT
                    count(*) AS players,
                    count(*) FILTER (WHERE rc_player_id IS NOT NULL) AS mapped,
                    count(*) FILTER (WHERE snap_count >= 2) AS trend_ready,
                    count(*) FILTER (WHERE snap_count = 1) AS one_snap,
                    count(*) FILTER (WHERE rc_player_id IS NOT NULL AND snap_count = 0) AS mapped_no_snaps
                FROM (
                    SELECT xp.id, xp.rc_player_id,
                           (SELECT count(*) FROM player_rating_snapshots prs
                            WHERE prs.player_id = xp.id AND prs.source = 'ratingscentral') AS snap_count
                    FROM xttv_players xp
                ) t
                """
            ),
        ).mappings().one()
    print("Global xttv_players:")
    for k, v in row.items():
        print(f"  {k}: {v}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", default="421 RK Linz Umg. / MV Ost")
    parser.add_argument("--global", dest="global_audit", action="store_true")
    args = parser.parse_args()
    if args.global_audit:
        audit_global()
    audit_league(args.league)


if __name__ == "__main__":
    main()
