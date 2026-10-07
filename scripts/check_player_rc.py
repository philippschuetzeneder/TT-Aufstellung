import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, "backend"))

from sqlalchemy import text

from app.db import SessionLocal, create_all
from app.models import RawSourceDocument, XttvPlayer
from app.rc_import import parse_player_history

name_pattern = sys.argv[1] if len(sys.argv) > 1 else "%Schützeneder%Philipp%"

create_all()
with SessionLocal() as db:
    rows = db.execute(
        text(
            """
            SELECT mp.external_player_id::text AS pass_id, max(mp.name) AS name
            FROM match_players mp
            WHERE mp.name ILIKE :pattern
            GROUP BY mp.external_player_id
            ORDER BY max(mp.name)
            """
        ),
        {"pattern": name_pattern},
    ).fetchall()
    print("Matches:", rows)
    if not rows:
        sys.exit(0)
    for pass_id, name in rows:
        p = db.query(XttvPlayer).filter_by(external_player_id=str(pass_id)).one_or_none()
        print(f"\n=== {name} pass={pass_id} ===")
        if not p:
            print("  kein xttv_players-Eintrag")
            continue
        print(f"  rc_player_id={p.rc_player_id} club={p.club}")
        stats = db.execute(
            text(
                """
                SELECT count(*) AS n, min(observed_at) AS first, max(observed_at) AS last,
                       max(imported_at) AS last_import
                FROM player_rating_snapshots
                WHERE player_id = :pid AND source = 'ratingscentral'
                """
            ),
            {"pid": p.id},
        ).mappings().one()
        print(f"  DB snapshots: {dict(stats)}")
        recent = db.execute(
            text(
                """
                SELECT observed_at::date, rc_rating, rc_deviation
                FROM player_rating_snapshots
                WHERE player_id = :pid AND source = 'ratingscentral'
                ORDER BY observed_at DESC
                LIMIT 8
                """
            ),
            {"pid": p.id},
        ).fetchall()
        print("  letzte Snapshots:")
        for r in recent:
            print(f"    {r[0]}  {r[1]}±{r[2]}")
        if p.rc_player_id:
            ext = f"playerhistory:{int(p.rc_player_id)}"
            raw = db.query(RawSourceDocument).filter_by(source="ratingscentral", external_id=ext).one_or_none()
            if raw and raw.content:
                parsed = parse_player_history(raw.content)
                hist = len(parsed.get("history") or [])
                print(f"  Cached PlayerHistory HTML: {hist} events, fetched_at={raw.fetched_at}")
            else:
                print("  Cached PlayerHistory HTML: fehlt")
