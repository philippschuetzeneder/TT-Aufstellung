"""Ensure Eder Alexander (25660) is mapped to RC 53585 with full snapshot history."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, "backend"))

from sqlalchemy import text

from app.analytics_service import _build_player_summary, _load_player_singles
from app.db import SessionLocal
from app.models import PlayerRatingSnapshot, XttvPlayer
from app.rc_import import import_rc_player

PASS_ID = "25660"
RC_ID = 53585

with SessionLocal() as session:
    player = session.query(XttvPlayer).filter_by(external_player_id=PASS_ID).one_or_none()
    if player is None:
        raise SystemExit(f"Pass {PASS_ID} nicht in der DB — zuerst XTTV-Import.")

    if player.rc_player_id not in (None, RC_ID):
        print(f"Entferne falsches RC-Mapping {player.rc_player_id}")
        player.rc_player_id = None
        session.commit()

result = import_rc_player(
    RC_ID,
    xttv_external_player_id=PASS_ID,
    xttv_name="Eder Alexander",
)
print("RC-Import:", result)

with SessionLocal() as session:
    player = session.query(XttvPlayer).filter_by(external_player_id=PASS_ID).one()
    snaps = (
        session.query(PlayerRatingSnapshot)
        .filter_by(player_id=player.id, source="ratingscentral")
        .order_by(PlayerRatingSnapshot.observed_at)
        .all()
    )
    singles = _load_player_singles(session, PASS_ID)
    summary = _build_player_summary(PASS_ID, player.name, player.club, singles, snaps)
    print(f"OK: rc_player_id={player.rc_player_id}, snapshots={len(snaps)}, rc={summary['rc_rating']}, trend={summary['rc_trend']}")
