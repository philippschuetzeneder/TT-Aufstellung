from sqlalchemy import text
from app.analysis_service import _effective_rc
from app.db import SessionLocal
from app.player_analysis_service import list_players

EXT = "23784"

with SessionLocal() as db:
    rows = db.execute(
        text(
            """
            SELECT s.rc_rating, s.rc_deviation, s.observed_at
            FROM xttv_players xp
            JOIN player_rating_snapshots s ON s.player_id = xp.id AND s.source = 'ratingscentral'
            WHERE xp.external_player_id = :e
            ORDER BY s.observed_at DESC
            LIMIT 8
            """
        ),
        {"e": EXT},
    ).mappings().all()
    print("snapshots:")
    for r in rows:
        eff = _effective_rc(r["rc_rating"], r["rc_deviation"])
        print(r["observed_at"], r["rc_rating"], "dev", r["rc_deviation"], "effective", eff)

lp = list_players("Tragwein/Kamig 2", league="421")
for p in lp["players"]:
    if p["id"] == EXT or "Friedinger" in (p.get("name") or ""):
        print("list_players:", p)
