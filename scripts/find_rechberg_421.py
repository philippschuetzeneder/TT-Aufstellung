"""Find Rechberg/Tragwein teams and players in league 421."""
from sqlalchemy import text
from app.db import SessionLocal, create_all

create_all()

with SessionLocal() as db:
    print("=== Leagues 421 ===")
    for row in db.execute(text(
        "SELECT id, name, season, match_count FROM leagues WHERE name LIKE '%421%' ORDER BY name"
    )).fetchall():
        print(row)

    print("\n=== Teams Rechberg / Tragwein in matches ===")
    for row in db.execute(text(
        """
        SELECT DISTINCT home_team, away_team, match_date
        FROM xttv_matches
        WHERE (home_team ILIKE '%Rechberg%' OR away_team ILIKE '%Rechberg%')
          AND (home_team ILIKE '%Tragwein%' OR away_team ILIKE '%Tragwein%')
        ORDER BY match_date DESC LIMIT 10
        """
    )).fetchall():
        print(row)

    print("\n=== Rechberg players (Ebenhofer Alexander, Waser Thomas) ===")
    for row in db.execute(text(
        """
        SELECT DISTINCT mp.external_player_id, mp.name, m.home_team, m.away_team, m.match_date
        FROM match_players mp JOIN xttv_matches m ON m.id = mp.match_id
        WHERE mp.name ILIKE '%Ebenhofer Alexander%' OR mp.name ILIKE '%Waser Thomas%'
        ORDER BY mp.name, m.match_date DESC LIMIT 20
        """
    )).fetchall():
        print(row)
