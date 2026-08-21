"""Export all unmapped XTTV players with best RC candidates for manual review."""
from __future__ import annotations

import csv
import sys
from pathlib import Path

from app.db import SessionLocal, create_all
from app.models import XttvPlayer
from app.rc_fallback import fallback_candidates
from app.rc_matching import _resolve_player, _rc_id_taken, rank_candidates


def _rating_cell(candidate: dict) -> str:
    for cell in candidate.get("cells") or []:
        text = str(cell)
        if "±" in text or "+/-" in text:
            return text
    return ""


def _best_candidates(ext: str, name: str, club: str | None) -> tuple[str, str, list[dict]]:
    resolved = _resolve_player(
        ext, name, club,
        allow_network_fallback=True,
        allow_live_fetch=True,
    )
    status = resolved.get("status") or ""
    reason = resolved.get("match_reason") or status
    candidates: list[dict] = []

    if status == "matched" and resolved.get("candidate"):
        rc_id = int(resolved["candidate"]["rc_player_id"])
        with SessionLocal() as session:
            conflict = _rc_id_taken(session, rc_id, ext)
        if conflict is not None:
            status = "conflict"
            reason = f"RC {rc_id} bereits an {conflict.external_player_id} ({conflict.name})"
            candidates = list(resolved.get("candidates") or [])
            if resolved.get("candidate"):
                candidates = [resolved["candidate"]] + [c for c in candidates if c.get("rc_player_id") != rc_id]
        else:
            candidates = [resolved["candidate"]]
    elif status == "ambiguous":
        ranked = resolved.get("candidates") or []
        exact = [c for c in ranked if c.get("name_score", 0) >= 95]
        candidates = exact or ranked[:5]
    elif status == "not_found":
        ranked = resolved.get("candidates") or []
        if ranked:
            candidates = ranked[:5]
        else:
            candidates = fallback_candidates(name, limit=5)

    return status, reason, candidates


def main() -> None:
    create_all()
    out_path = Path(__file__).resolve().parents[1] / "data" / "unmapped_rc_review.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with SessionLocal() as session:
        unmapped = (
            session.query(XttvPlayer)
            .filter(XttvPlayer.rc_player_id.is_(None))
            .order_by(XttvPlayer.name)
            .all()
        )

    rows: list[dict] = []
    for player in unmapped:
        ext = str(player.external_player_id)
        status, reason, candidates = _best_candidates(ext, player.name, player.club)
        if not candidates:
            rows.append({
                "xttv_id": ext,
                "name": player.name,
                "club": player.club or "",
                "status": status,
                "reason": reason,
                "best_rc_id": "",
                "best_rc_name": "",
                "best_rating": "",
                "best_score": "",
                "alt_candidates": "",
            })
            continue

        best = candidates[0]
        alts = candidates[1:5]
        alt_parts = []
        for c in alts:
            rating = _rating_cell(c)
            score = c.get("match_score") or c.get("fallback_score") or c.get("name_score") or ""
            alt_parts.append(
                f"RC {c.get('rc_player_id')} | {c.get('name', '')} | {rating} | score={score}"
            )

        rows.append({
            "xttv_id": ext,
            "name": player.name,
            "club": player.club or "",
            "status": status,
            "reason": reason,
            "best_rc_id": best.get("rc_player_id", ""),
            "best_rc_name": best.get("name", ""),
            "best_rating": _rating_cell(best),
            "best_score": best.get("match_score") or best.get("fallback_score") or best.get("name_score") or "",
            "alt_candidates": " || ".join(alt_parts),
        })

    fieldnames = list(rows[0].keys()) if rows else []
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"unmapped={len(rows)} -> {out_path}")
    for row in rows:
        print(
            f"{row['xttv_id']}\t{row['name']}\t{row['club']}\t{row['status']}\t"
            f"RC {row['best_rc_id']} {row['best_rc_name']} {row['best_rating']}\t{row['reason']}"
        )


if __name__ == "__main__":
    main()
