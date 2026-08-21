"""Import RC rating history for all mapped players missing ratingscentral snapshots."""
from __future__ import annotations

from app.db import SessionLocal, create_all
from app.models import XttvPlayer, PlayerRatingSnapshot
from app.rc_import import import_rc_player


def main() -> None:
    create_all()
    with SessionLocal() as session:
        mapped = session.query(XttvPlayer).filter(XttvPlayer.rc_player_id.isnot(None)).all()
        with_snap = {
            row[0]
            for row in session.query(PlayerRatingSnapshot.player_id)
            .filter(PlayerRatingSnapshot.source == "ratingscentral")
            .distinct()
            .all()
        }
        targets = [p for p in mapped if p.id not in with_snap]

    print(f"mapped={len(mapped)} missing_history={len(targets)}")
    ok = err = 0
    for i, player in enumerate(targets, 1):
        try:
            result = import_rc_player(
                int(player.rc_player_id),
                xttv_external_player_id=str(player.external_player_id),
                xttv_name=player.name,
                xttv_club=player.club,
            )
            ok += 1
            if i % 25 == 0 or i == len(targets):
                print(f"  {i}/{len(targets)} ok (last obs={result.get('historical_observations')})")
        except Exception as exc:
            err += 1
            print(f"ERR {player.external_player_id} {player.name}: {exc}")

    with SessionLocal() as session:
        still = 0
        for p in session.query(XttvPlayer).filter(XttvPlayer.rc_player_id.isnot(None)).all():
            has = session.query(PlayerRatingSnapshot).filter_by(
                player_id=p.id, source="ratingscentral"
            ).first()
            if not has:
                still += 1

    print(f"done imported={ok} errors={err} still_without_history={still}")


if __name__ == "__main__":
    main()
