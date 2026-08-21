from sqlalchemy import text
from app.db import SessionLocal

with SessionLocal() as db:
    n = db.execute(text("SELECT count(*) FROM match_games WHERE game_type = 'doubles'")).scalar()
    rows = db.execute(
        text(
            "SELECT home_player, away_player, result, sequence "
            "FROM match_games WHERE game_type = 'doubles' LIMIT 8"
        )
    ).fetchall()
    print("doubles games", n)
    for row in rows:
        print(row)
