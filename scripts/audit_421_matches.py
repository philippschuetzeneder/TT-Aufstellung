"""Compare DB match count for 421 2025/2026 vs XTTV schedule."""
from sqlalchemy import text

from app.db import SessionLocal, create_all
from app.player_analysis_service import list_leagues, CURRENT_SEASON

create_all()

with SessionLocal() as db:
    leagues = list_leagues()["leagues"]
    l421 = next((l for l in leagues if l["name"].startswith("421 ")), None)
    print("list_leagues 421:", l421)
    if not l421:
        raise SystemExit("421 not in list")

    resolved = l421["latest_league"]
    db_count = db.execute(
        text("SELECT COUNT(*) FROM xttv_matches WHERE league = :l"),
        {"l": resolved},
    ).scalar()
    print(f"DB count ({resolved}): {db_count}")

    matches = db.execute(
        text(
            """
            SELECT external_id, match_date, home_team, away_team, title, season
            FROM xttv_matches
            WHERE league = :l
            ORDER BY match_date, external_id
            """
        ),
        {"l": resolved},
    ).mappings().all()
    print(f"Matches loaded: {len(matches)}")

    # Check for duplicates, missing external_ids
    ext_ids = [m["external_id"] for m in matches]
    print(f"Unique external_ids: {len(set(ext_ids))}")

    # Any 421 2025/2026 with different league string?
    alt = db.execute(
        text(
            """
            SELECT league, COUNT(*) FROM xttv_matches
            WHERE league LIKE '421%' AND league LIKE '%2025/2026%'
            GROUP BY league ORDER BY league
            """
        )
    ).fetchall()
    print("All 421* 2025/2026 league strings:", alt)

    # Matches without full player data?
    incomplete = db.execute(
        text(
            """
            SELECT m.external_id, m.match_date, m.home_team, m.away_team,
                   COUNT(mp.id) AS players
            FROM xttv_matches m
            LEFT JOIN match_players mp ON mp.match_id = m.id
            WHERE m.league = :l
            GROUP BY m.id, m.external_id, m.match_date, m.home_team, m.away_team
            HAVING COUNT(mp.id) < 8
            ORDER BY m.match_date
            """
        ),
        {"l": resolved},
    ).mappings().all()
    print(f"Matches with <8 players: {len(incomplete)}")
    for row in incomplete:
        print(dict(row))
