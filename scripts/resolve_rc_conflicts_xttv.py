"""Resolve RC conflicts: XTTV pass lookup only, no ping-pong remapping."""
from __future__ import annotations

import time

from app.db import SessionLocal, create_all
from app.models import PlayerRatingSnapshot, XttvPlayer
from app.rc_import import import_rc_player
from app.rc_manual_overrides import RC_PLAYER_OVERRIDES
from app.xttv_rc_lookup import lookup_rc_player_id


def xttv_rc_for_pass(pass_id: str, name: str, retries: int = 3) -> int | None:
    override = RC_PLAYER_OVERRIDES.get(str(pass_id))
    if override is not None:
        return int(override)
    for attempt in range(retries):
        lookup = lookup_rc_player_id(pass_id=pass_id, name=None)
        if lookup.get("ok") and lookup.get("rc_player_id"):
            return int(lookup["rc_player_id"])
        if "500" not in str(lookup.get("reason", "")):
            break
        time.sleep(1.0 * (attempt + 1))
    return None


def apply_mapping(player: XttvPlayer, rc_id: int) -> None:
    with SessionLocal() as session:
        db = session.query(XttvPlayer).filter_by(id=player.id).one()
        db.rc_player_id = rc_id
        session.commit()
    import_rc_player(
        rc_id,
        xttv_external_player_id=str(player.external_player_id),
        xttv_name=player.name,
        xttv_club=player.club,
    )


def main() -> None:
    create_all()
    with SessionLocal() as session:
        targets = (
            session.query(XttvPlayer)
            .filter(XttvPlayer.rc_player_id.is_(None))
            .order_by(XttvPlayer.name)
            .all()
        )

    print(f"unmapped={len(targets)}")
    plans: list[tuple[XttvPlayer, int]] = []
    skipped: list[tuple[str, str, str]] = []

    for player in targets:
        ext = str(player.external_player_id)
        rc_id = xttv_rc_for_pass(ext, player.name)
        if rc_id is None:
            skipped.append((ext, player.name, "no XTTV RC-Graph (pass lookup)"))
            continue
        plans.append((player, rc_id))

    # Free RC IDs currently held by other passes when this pass is authoritative.
    for player, rc_id in plans:
        ext = str(player.external_player_id)
        with SessionLocal() as session:
            holders = (
                session.query(XttvPlayer)
                .filter(XttvPlayer.rc_player_id == rc_id)
                .filter(XttvPlayer.external_player_id != ext)
                .all()
            )
            for holder in holders:
                holder_rc = xttv_rc_for_pass(str(holder.external_player_id), holder.name)
                if holder_rc != rc_id:
                    h = session.query(XttvPlayer).filter_by(id=holder.id).one()
                    h.rc_player_id = None
                    print(f"free {holder.external_player_id} {holder.name} (had RC {rc_id}, XTTV says {holder_rc})")
            session.commit()

    for player, rc_id in plans:
        ext = str(player.external_player_id)
        apply_mapping(player, rc_id)
        print(f"mapped {ext} {player.name} -> RC {rc_id}")

    # Re-resolve freed holders that became unmapped.
    with SessionLocal() as session:
        freed = (
            session.query(XttvPlayer)
            .filter(XttvPlayer.rc_player_id.is_(None))
            .order_by(XttvPlayer.name)
            .all()
        )
    for player in freed:
        ext = str(player.external_player_id)
        rc_id = xttv_rc_for_pass(ext, player.name)
        if rc_id is None:
            skipped.append((ext, player.name, "no XTTV RC-Graph after free"))
            continue
        apply_mapping(player, rc_id)
        print(f"remapped {ext} {player.name} -> RC {rc_id}")

    with SessionLocal() as session:
        remaining = session.query(XttvPlayer).filter(XttvPlayer.rc_player_id.is_(None)).count()
        mapped = session.query(XttvPlayer).filter(XttvPlayer.rc_player_id.isnot(None)).count()
        no_hist = sum(
            1
            for p in session.query(XttvPlayer).filter(XttvPlayer.rc_player_id.isnot(None))
            if not session.query(PlayerRatingSnapshot).filter_by(player_id=p.id, source="ratingscentral").first()
        )

    print(f"\nmapped={mapped} unmapped={remaining} no_history={no_hist}")
    print("\n=== STILL OPEN ===")
    for ext, name, reason in skipped:
        print(f"  {ext} | {name} | {reason}")
    with SessionLocal() as session:
        for p in session.query(XttvPlayer).filter(XttvPlayer.rc_player_id.is_(None)).order_by(XttvPlayer.name):
            if not any(s[0] == str(p.external_player_id) for s in skipped):
                print(f"  {p.external_player_id} | {p.name} | still unmapped")


if __name__ == "__main__":
    main()
