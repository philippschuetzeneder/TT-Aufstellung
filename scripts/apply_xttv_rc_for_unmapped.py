"""Apply RC mappings from XTTV Spielersuche RC-Graph for all unmapped players."""
from __future__ import annotations

from app.db import SessionLocal, create_all
from app.models import XttvPlayer
from app.rc_import import import_rc_player
from app.rc_matching import _rc_id_taken
from app.xttv_rc_lookup import lookup_rc_player_id


def main() -> None:
    create_all()
    with SessionLocal() as session:
        unmapped = (
            session.query(XttvPlayer)
            .filter(XttvPlayer.rc_player_id.is_(None))
            .order_by(XttvPlayer.name)
            .all()
        )

    print(f"unmapped_at_start={len(unmapped)}")
    applied = conflict = not_found = errors = 0
    still_open: list[dict] = []

    for player in unmapped:
        ext = str(player.external_player_id)
        lookup = lookup_rc_player_id(pass_id=ext, name=player.name)
        if not lookup.get("ok") or lookup.get("rc_player_id") is None:
            not_found += 1
            still_open.append({
                "xttv_id": ext,
                "name": player.name,
                "club": player.club or "",
                "reason": lookup.get("reason") or "no RC-Graph on XTTV",
            })
            continue

        rc_id = int(lookup["rc_player_id"])
        with SessionLocal() as session:
            conflict_player = _rc_id_taken(session, rc_id, ext)
            if conflict_player is not None:
                conflict += 1
                still_open.append({
                    "xttv_id": ext,
                    "name": player.name,
                    "club": player.club or "",
                    "reason": f"RC {rc_id} bereits an {conflict_player.external_player_id} ({conflict_player.name})",
                    "xttv_rc_id": rc_id,
                })
                continue

            db_player = session.query(XttvPlayer).filter_by(external_player_id=ext).one_or_none()
            if db_player is None:
                errors += 1
                still_open.append({
                    "xttv_id": ext,
                    "name": player.name,
                    "club": player.club or "",
                    "reason": "XTTV player missing in DB",
                })
                continue

            db_player.rc_player_id = rc_id
            session.commit()

        try:
            import_rc_player(
                rc_id,
                xttv_external_player_id=ext,
                xttv_name=player.name,
                xttv_club=player.club,
            )
            applied += 1
            print(f"OK {ext} {player.name} -> RC {rc_id} ({lookup.get('method')})")
        except Exception as exc:
            errors += 1
            still_open.append({
                "xttv_id": ext,
                "name": player.name,
                "club": player.club or "",
                "reason": f"import error: {exc}",
                "xttv_rc_id": rc_id,
            })

    with SessionLocal() as session:
        remaining = session.query(XttvPlayer).filter(XttvPlayer.rc_player_id.is_(None)).count()
        mapped = session.query(XttvPlayer).filter(XttvPlayer.rc_player_id.isnot(None)).count()

    print()
    print(f"applied={applied} conflict={conflict} not_found={not_found} errors={errors}")
    print(f"mapped={mapped} still_unmapped={remaining}")
    print()
    print("=== STILL OPEN ===")
    for row in still_open:
        club = f" · {row['club']}" if row.get("club") else ""
        rc = f" (XTTV RC {row['xttv_rc_id']})" if row.get("xttv_rc_id") else ""
        print(f"  {row['xttv_id']} | {row['name']}{club}{rc} — {row['reason']}")


if __name__ == "__main__":
    main()
