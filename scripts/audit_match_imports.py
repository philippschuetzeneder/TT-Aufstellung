"""Audit Sandl vs Tragwein match imports."""
from __future__ import annotations

from app.db import SessionLocal
from app.xttv_db_import import import_one
from sqlalchemy import text

TEAMS = ("Sandl 1", "Tragwein/Kamig 3")
KNOWN_MEIDS = (437825,)


def main():
    db = SessionLocal()
    rows = db.execute(
        text(
            """
            SELECT external_id, match_date, home_team, away_team, team_result
            FROM xttv_matches
            WHERE (
                (home_team = :home AND away_team = :away)
                OR (home_team = :away AND away_team = :home)
            )
            ORDER BY match_date
            """
        ),
        {"home": TEAMS[0], "away": TEAMS[1]},
    ).fetchall()
    print(f"Direct Sandl 1 vs Tragwein/Kamig 3 in DB: {len(rows)}")
    for r in rows:
        print(f"  meid={r[0]} date={r[1]} {r[2]} vs {r[3]} result={r[4]}")

    for meid in KNOWN_MEIDS:
        row = db.execute(
            text("SELECT external_id, match_date, home_team, away_team FROM xttv_matches WHERE external_id = :id"),
            {"id": str(meid)},
        ).fetchone()
        if row:
            print(f"Known meid {meid}: imported ({row[1]}, {row[2]} vs {row[3]})")
        else:
            print(f"Known meid {meid}: MISSING — importing...")
            result = import_one(meid)
            print(f"  import result: {result.get('ok')} {result.get('status', result.get('error', ''))}")

    # Cross-check H2H Philipp vs Melanie after import
    h2h = db.execute(
        text(
            """
            WITH base AS (
                SELECT hp.external_player_id::text AS home_id, ap.external_player_id::text AS away_id,
                       m.external_id AS meid,
                       g.result,
                       CASE WHEN split_part(trim(g.result),':',1)::int > split_part(trim(g.result),':',2)::int THEN 1 ELSE 0 END AS home_win
                FROM match_games g
                JOIN match_players hp ON hp.match_id=g.match_id AND hp.side='home' AND hp.position=g.home_position
                JOIN match_players ap ON ap.match_id=g.match_id AND ap.side='away' AND ap.position=g.away_position
                JOIN xttv_matches m ON m.id = g.match_id
                WHERE g.game_type='singles' AND g.result ~ '^\\s*[0-9]+\\s*:\\s*[0-9]+\\s*$'
                  AND hp.external_player_id = '21773' AND ap.external_player_id = '70417'
            )
            SELECT meid, result, home_win FROM base ORDER BY meid
            """
        )
    ).fetchall()
    print(f"\nH2H Schützeneder vs Riepl Melanie (raw games): {len(h2h)}")
    wins = sum(r[2] for r in h2h)
    for r in h2h:
        print(f"  meid={r[0]} result={r[1]} philipp_win={r[2]}")
    print(f"  Total: {wins}/{len(h2h)}")

    db.close()


if __name__ == "__main__":
    main()
