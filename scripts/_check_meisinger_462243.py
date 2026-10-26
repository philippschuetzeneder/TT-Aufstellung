from sqlalchemy import text
from app.db import SessionLocal

with SessionLocal() as db:
    mp = db.execute(
        text(
            """
            SELECT mp.external_player_id, mp.name, mp.side, mp.position
            FROM match_players mp
            JOIN xttv_matches m ON m.id = mp.match_id
            WHERE m.external_id = '462243'
            ORDER BY mp.side, mp.position
            """
        ),
    ).mappings().all()
    print("lineup_462243", [dict(r) for r in mp])
    meid = db.execute(
        text("SELECT id, match_date, imported_at FROM xttv_matches WHERE external_id='462243'"),
    ).mappings().first()
    print("match_meta", dict(meid) if meid else None)
