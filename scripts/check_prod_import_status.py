"""Prod helper: season counts, 421 MEIDs, scan frontier."""
from __future__ import annotations

from sqlalchemy import text

from app.db import SessionLocal, create_all
from app.player_analysis_service import current_season, list_leagues

MEIDS_421 = ["462229", "462230", "462231", "462232", "462233"]


def main() -> None:
    create_all()
    season = current_season()
    print("current_season", season)
    with SessionLocal() as session:
        rows = session.execute(
            text(
                "SELECT season, COUNT(1) FROM xttv_matches "
                "GROUP BY season ORDER BY season"
            ),
        ).all()
        print("by_season", rows)
        frontier = session.execute(
            text("SELECT value FROM xttv_scan_meta WHERE key = 'frontier_meid'"),
        ).scalar()
        print("frontier_meid", frontier)
        max_meid = session.execute(
            text("SELECT MAX(external_id::bigint) FROM xttv_matches"),
        ).scalar()
        print("max_imported_meid", max_meid)
        gap = session.execute(
            text(
                "SELECT COUNT(1) FROM xttv_matches "
                "WHERE external_id::bigint BETWEEN 448190 AND 462232"
            ),
        ).scalar()
        print("matches_in_gap_448190_462232", gap)
        present = session.execute(
            text(
                "SELECT external_id FROM xttv_matches "
                "WHERE external_id = ANY(:ids) ORDER BY external_id::bigint"
            ),
            {"ids": MEIDS_421},
        ).fetchall()
        print("421_schedule_meids_in_db", [r[0] for r in present])
    leagues = list_leagues()
    l421 = next((x for x in leagues["leagues"] if x["name"].startswith("421 ")), None)
    print("list_leagues_421", l421)


if __name__ == "__main__":
    main()
