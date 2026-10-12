import sys
from sqlalchemy import text
from app.db import SessionLocal

ids = sys.argv[1:]
with SessionLocal() as db:
    rows = db.execute(
        text(
            "SELECT external_id, home_team, away_team, team_result FROM xttv_matches "
            "WHERE external_id = ANY(:ids)"
        ),
        {"ids": ids},
    ).all()
print("found", len(rows))
for r in rows:
    print(r)
