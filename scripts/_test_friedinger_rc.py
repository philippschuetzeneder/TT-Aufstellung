from app.db import SessionLocal
from app.models import XttvPlayer
from app.rc_import import import_rc_player_updates
from sqlalchemy import text

p = SessionLocal().query(XttvPlayer).filter_by(external_player_id="23784").one()
print(import_rc_player_updates(int(p.rc_player_id), xttv_external_player_id="23784"))
row = SessionLocal().execute(
    text(
        """
        SELECT observed_at::date, rc_rating FROM player_rating_snapshots s
        JOIN xttv_players xp ON xp.id=s.player_id
        WHERE xp.external_player_id='23784' AND s.source='ratingscentral'
        ORDER BY observed_at DESC LIMIT 3
        """
    )
).all()
print("snapshots", row)
