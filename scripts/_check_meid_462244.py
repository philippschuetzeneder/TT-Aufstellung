from sqlalchemy import text

from app.data_refresh_service import external_ids_needing_rc_after_match
from app.db import SessionLocal

MEID = "462244"


def main() -> None:
    with SessionLocal() as db:
        m = db.execute(
            text(
                "SELECT external_id, match_date, home_team, away_team, league, season "
                "FROM xttv_matches WHERE external_id = :m"
            ),
            {"m": MEID},
        ).mappings().first()
        print("in_db", dict(m) if m else None)
        if m:
            mp = db.execute(
                text(
                    """
                    SELECT external_player_id, name, side, position
                    FROM match_players mp
                    JOIN xttv_matches xm ON xm.id = mp.match_id
                    WHERE xm.external_id = :m
                    ORDER BY side, position
                    """
                ),
                {"m": MEID},
            ).mappings().all()
            print("lineup", [dict(r) for r in mp])
            for pid in ("17885", "23754", "23784", "21771", "22183"):
                rc = db.execute(
                    text(
                        """
                        SELECT prs.rc_rating, prs.observed_at
                        FROM player_rating_snapshots prs
                        JOIN xttv_players p ON p.id = prs.player_id
                        WHERE p.external_player_id::text = :p
                          AND prs.source = 'ratingscentral'
                        ORDER BY prs.observed_at DESC LIMIT 1
                        """
                    ),
                    {"p": pid},
                ).mappings().first()
                print("rc", pid, dict(rc) if rc else None)
    stale = external_ids_needing_rc_after_match()
    for pid in ("17885", "23754", "23784", "21771", "22183"):
        print("stale", pid, pid in stale)


if __name__ == "__main__":
    main()
