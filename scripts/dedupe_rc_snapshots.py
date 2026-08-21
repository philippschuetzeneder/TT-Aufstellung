"""Remove obvious duplicate RC rating snapshots (same rating+deviation within 2 days)."""
from __future__ import annotations

from app.db import SessionLocal, create_all
from app.models import PlayerRatingSnapshot


def find_duplicate_ids() -> list[int]:
    create_all()
    with SessionLocal() as session:
        snaps = (
            session.query(PlayerRatingSnapshot)
            .filter_by(source="ratingscentral")
            .order_by(PlayerRatingSnapshot.player_id, PlayerRatingSnapshot.observed_at)
            .all()
        )
    dup_ids: list[int] = []
    prev: PlayerRatingSnapshot | None = None
    for snap in snaps:
        if prev is not None and snap.player_id == prev.player_id:
            same_rating = snap.rc_rating == prev.rc_rating
            same_dev = snap.rc_deviation == prev.rc_deviation
            days = abs((snap.observed_at - prev.observed_at).days)
            if same_rating and same_dev and days <= 2:
                dup_ids.append(snap.id)
                continue
        prev = snap
    return dup_ids


def main() -> None:
    dup_ids = find_duplicate_ids()
    print(f"duplicate_snapshots_to_remove={len(dup_ids)}")
    if not dup_ids:
        return
    with SessionLocal() as session:
        removed = (
            session.query(PlayerRatingSnapshot)
            .filter(PlayerRatingSnapshot.id.in_(dup_ids))
            .delete(synchronize_session=False)
        )
        session.commit()
    print(f"removed={removed}")


if __name__ == "__main__":
    main()
