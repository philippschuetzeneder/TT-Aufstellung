"""Run full RC apply loop and print summary."""
from __future__ import annotations

from app.db import SessionLocal, create_all
from app.models import XttvPlayer
from app.rc_matching import apply_matches_all, _resolve_player, _rc_id_taken


def main() -> None:
    create_all()
    with SessionLocal() as session:
        total = session.query(XttvPlayer).count()
        mapped_before = session.query(XttvPlayer).filter(XttvPlayer.rc_player_id.isnot(None)).count()
        unmapped_before = total - mapped_before

    print(f"BEFORE total={total} mapped={mapped_before} unmapped={unmapped_before}")

    winter = _resolve_player("23697", "Winter Philipp", "Sandl 2", allow_live_fetch=True)
    cand = winter.get("candidate") or {}
    print(
        f"Winter Philipp: status={winter['status']} "
        f"rc={cand.get('rc_player_id')} reason={winter.get('match_reason')}"
    )

    result = apply_matches_all(batch_size=500, import_history=False, only_unmapped=True)
    print(
        "APPLY:",
        {k: result[k] for k in [
            "applied", "ambiguous", "not_found", "conflicts", "errors",
            "batches_processed", "players_with_rc_id_after",
        ]},
    )

    with SessionLocal() as session:
        mapped_after = session.query(XttvPlayer).filter(XttvPlayer.rc_player_id.isnot(None)).count()
        unmapped_after = total - mapped_after

    print(f"AFTER mapped={mapped_after} unmapped={unmapped_after} newly_mapped={mapped_after - mapped_before}")

    # Breakdown of remaining unmapped
    with SessionLocal() as session:
        unmapped = (
            session.query(XttvPlayer)
            .filter(XttvPlayer.rc_player_id.is_(None))
            .order_by(XttvPlayer.name)
            .all()
        )

    counts = {
        "not_found": 0,
        "ambiguous": 0,
        "conflict": 0,
        "ready_to_apply": 0,
        "other": 0,
    }
    for player in unmapped:
        ext = str(player.external_player_id)
        resolved = _resolve_player(ext, player.name, player.club, allow_live_fetch=True)
        status = resolved.get("status")
        if status == "matched" and resolved.get("candidate"):
            rc_id = int(resolved["candidate"]["rc_player_id"])
            with SessionLocal() as session:
                conflict = _rc_id_taken(session, rc_id, ext)
            if conflict is not None:
                counts["conflict"] += 1
            else:
                counts["ready_to_apply"] += 1
        elif status == "ambiguous":
            counts["ambiguous"] += 1
        elif status == "not_found":
            counts["not_found"] += 1
        else:
            counts["other"] += 1

    print("REMAINING BREAKDOWN:", counts)


if __name__ == "__main__":
    main()
