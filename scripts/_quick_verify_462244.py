from sqlalchemy import text
from app.db import SessionLocal

with SessionLocal() as db:
    m = db.execute(
        text(
            "SELECT external_id, match_date, home_team, away_team, league "
            "FROM xttv_matches WHERE external_id = '462244'"
        ),
    ).mappings().first()
    print("462244", dict(m) if m else None)
    mp = db.execute(
        text(
            """
            SELECT mp.external_player_id, mp.name
            FROM match_players mp
            JOIN xttv_matches xm ON xm.id = mp.match_id
            WHERE xm.external_id = '462244' AND mp.external_player_id::text = '17885'
            """
        ),
    ).mappings().first()
    print("meisinger_in_match", dict(mp) if mp else None)
    rc = db.execute(
        text(
            """
            SELECT prs.rc_rating, prs.observed_at
            FROM player_rating_snapshots prs
            JOIN xttv_players p ON p.id = prs.player_id
            WHERE p.external_player_id::text = '17885'
            ORDER BY prs.observed_at DESC LIMIT 3
            """
        ),
    ).mappings().all()
    print("meisinger_rc", [dict(r) for r in rc])
