"""Diagnose RC snapshot depth: DB vs cached RawSourceDocument vs live fetch."""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, "backend"))

from sqlalchemy import text

from app.db import SessionLocal, create_all
from app.models import RawSourceDocument, XttvPlayer
from app.rc_import import fetch_player_history, parse_player_history


def main() -> None:
    create_all()
    with SessionLocal() as db:
        summary = db.execute(
            text(
                """
                SELECT
                    count(*) AS players,
                    count(*) FILTER (WHERE rc_player_id IS NOT NULL) AS mapped,
                    count(*) FILTER (WHERE snap_count = 0) AS zero,
                    count(*) FILTER (WHERE snap_count = 1) AS one,
                    count(*) FILTER (WHERE snap_count BETWEEN 2 AND 5) AS few,
                    count(*) FILTER (WHERE snap_count > 5) AS many,
                    max(snap_count) AS max_snaps
                FROM (
                    SELECT xp.id, xp.rc_player_id,
                           (SELECT count(*) FROM player_rating_snapshots prs
                            WHERE prs.player_id = xp.id AND prs.source = 'ratingscentral') AS snap_count
                    FROM xttv_players xp
                    WHERE xp.rc_player_id IS NOT NULL
                ) t
                """
            ),
        ).mappings().one()
        print("=== Snapshot depth (mapped players) ===")
        for k, v in summary.items():
            print(f"  {k}: {v}")

        dup_dates = db.execute(
            text(
                """
                SELECT count(*) FROM (
                    SELECT player_id, observed_at::date, count(*) AS c
                    FROM player_rating_snapshots
                    WHERE source = 'ratingscentral'
                    GROUP BY 1, 2
                    HAVING count(*) > 1
                ) d
                """
            ),
        ).scalar()
        print(f"  duplicate (player, date) groups: {dup_dates}")

        raw_hist = db.execute(
            text(
                """
                SELECT count(*) FROM raw_source_documents
                WHERE source = 'ratingscentral' AND external_id LIKE 'playerhistory:%'
                """
            ),
        ).scalar()
        print(f"\n=== Raw RC PlayerHistory HTML cached: {raw_hist} documents ===")

        samples = db.execute(
            text(
                """
                SELECT xp.external_player_id, xp.name, xp.rc_player_id,
                       (SELECT count(*) FROM player_rating_snapshots prs
                        WHERE prs.player_id = xp.id AND prs.source = 'ratingscentral') AS snaps,
                       (SELECT max(prs.imported_at) FROM player_rating_snapshots prs
                        WHERE prs.player_id = xp.id AND prs.source = 'ratingscentral') AS last_import
                FROM xttv_players xp
                WHERE xp.rc_player_id IS NOT NULL
                ORDER BY snaps ASC, xp.name
                LIMIT 8
                """
            ),
        ).mappings().all()

        print("\n=== Low-snapshot samples ===")
        for row in samples:
            print(dict(row))

        # Meisinger + one 1-snap league player
        for pass_id, label in (("17885", "Meisinger"), ("15752", "Eder Andreas")):
            p = db.query(XttvPlayer).filter_by(external_player_id=pass_id).one_or_none()
            if not p or not p.rc_player_id:
                print(f"\n{label}: not found / unmapped")
                continue
            rc_id = int(p.rc_player_id)
            snaps = db.execute(
                text(
                    "SELECT count(*), min(observed_at), max(observed_at) FROM player_rating_snapshots "
                    "WHERE player_id = :pid AND source = 'ratingscentral'"
                ),
                {"pid": p.id},
            ).one()
            ext = f"playerhistory:{rc_id}"
            raw = db.query(RawSourceDocument).filter_by(source="ratingscentral", external_id=ext).one_or_none()
            raw_events = None
            if raw and raw.content:
                parsed = parse_player_history(raw.content)
                raw_events = len(parsed.get("history") or [])
            print(f"\n--- {label} pass={pass_id} rc={rc_id} ---")
            print(f"  DB snapshots: {snaps[0]}  range {snaps[1]} .. {snaps[2]}")
            print(f"  Cached HTML events (history list): {raw_events}")
            if raw:
                print(f"  Raw doc fetched_at: {raw.fetched_at}")

        # When were 1-snap players last imported?
        one_snap_import = db.execute(
            text(
                """
                SELECT date_trunc('day', max(prs.imported_at)) AS day, count(DISTINCT xp.id) AS players
                FROM xttv_players xp
                JOIN player_rating_snapshots prs ON prs.player_id = xp.id AND prs.source = 'ratingscentral'
                GROUP BY xp.id
                HAVING count(*) = 1
                """
            ),
        ).fetchall()
        if one_snap_import:
            from collections import Counter
            days = Counter(str(row[0])[:10] for row in one_snap_import)
            print("\n=== Import day for players with exactly 1 snapshot (top) ===")
            for day, n in days.most_common(5):
                print(f"  {day}: {n} players")


if __name__ == "__main__":
    main()
