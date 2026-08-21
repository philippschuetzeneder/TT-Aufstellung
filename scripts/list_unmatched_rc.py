"""List all unmatched XTTV players with RC matching reason."""
from __future__ import annotations

from collections import defaultdict

from app.db import SessionLocal, create_all
from app.models import XttvPlayer
from app.rc_matching import _resolve_player, _rc_id_taken


def main():
    create_all()
    with SessionLocal() as session:
        total = session.query(XttvPlayer).count()
        mapped = session.query(XttvPlayer).filter(XttvPlayer.rc_player_id.isnot(None)).count()
        unmapped = (
            session.query(XttvPlayer)
            .filter(XttvPlayer.rc_player_id.is_(None))
            .order_by(XttvPlayer.name)
            .all()
        )

    print(f"TOTAL {total} | mapped {mapped} | unmapped {len(unmapped)}")
    print()

    by_reason: dict[str, list[dict]] = defaultdict(list)

    for player in unmapped:
        ext = str(player.external_player_id)
        resolved = _resolve_player(
            ext, player.name, player.club,
            allow_network_fallback=False,
            allow_live_fetch=True,
        )
        status = resolved.get("status")
        reason = resolved.get("match_reason") or status
        detail = ""

        if status == "matched" and resolved.get("candidate"):
            rc_id = int(resolved["candidate"]["rc_player_id"])
            with SessionLocal() as session:
                conflict = _rc_id_taken(session, rc_id, ext)
            if conflict is not None:
                status = "conflict"
                reason = "RC-ID bereits an anderen Spieler vergeben"
                detail = f"RC {rc_id} -> {conflict.name} ({conflict.external_player_id})"
            else:
                status = "ready_to_apply"
                reason = "Match möglich, noch nicht angewendet"
                detail = f"RC {rc_id} ({resolved['candidate'].get('name', '')})"

        if status == "ambiguous":
            candidates = resolved.get("candidates") or []
            exact = [c for c in candidates if c.get("name_score", 0) >= 95]
            names = ", ".join(
                f"RC {c['rc_player_id']} ({c.get('name', '?')})" for c in exact[:5]
            )
            detail = names or "mehrere Kandidaten"

        if status == "not_found":
            fb = resolved.get("candidates") or []
            if fb:
                reason = "Kein exakter Name in RC; Fuzzy-Kandidaten nicht sicher genug"
                detail = ", ".join(f"RC {c['rc_player_id']}" for c in fb[:3])
            else:
                reason = "Nicht in Ratings Central gefunden (kein Kandidat)"

        entry = {
            "id": ext,
            "name": player.name,
            "club": player.club or "",
            "detail": detail,
        }
        by_reason[reason].append(entry)

    for reason in sorted(by_reason.keys(), key=lambda r: (-len(by_reason[r]), r)):
        items = by_reason[reason]
        print(f"=== {reason} ({len(items)}) ===")
        for item in items:
            club = f" · {item['club']}" if item["club"] else ""
            det = f" — {item['detail']}" if item["detail"] else ""
            print(f"  {item['id']} | {item['name']}{club}{det}")
        print()


if __name__ == "__main__":
    main()
