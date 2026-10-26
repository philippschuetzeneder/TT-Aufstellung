"""Prod: Meisinger RC vs DB matches vs live RC."""
from sqlalchemy import text

from app.data_refresh_service import external_ids_needing_rc_after_match
from app.db import SessionLocal
from app.rc_import import fetch_player_history, parse_player_history

PASS = "17885"
RC_ID = 52929


def main() -> None:
    stale = external_ids_needing_rc_after_match()
    print("stale_list", PASS in stale, "stale_count", len(stale))
    with SessionLocal() as db:
        snaps = db.execute(
            text(
                """
                SELECT prs.rc_rating, prs.observed_at
                FROM player_rating_snapshots prs
                JOIN xttv_players p ON p.id = prs.player_id
                WHERE p.external_player_id::text = :p AND prs.source = 'ratingscentral'
                ORDER BY prs.observed_at DESC LIMIT 3
                """
            ),
            {"p": PASS},
        ).mappings().all()
        print("db_rc", [dict(r) for r in snaps])
        rows = db.execute(
            text(
                """
                SELECT external_id, match_date, home_team, away_team
                FROM xttv_matches
                WHERE season = '2026/2027'
                  AND league ILIKE '%421%'
                  AND (home_team ILIKE '%Tragwein%' OR away_team ILIKE '%Tragwein%')
                ORDER BY to_timestamp(
                    (regexp_match(match_date, '(\\d{2}\\.\\d{2}\\.\\d{4})'))[1],
                    'DD.MM.YYYY'
                ) DESC
                LIMIT 15
                """
            ),
        ).mappings().all()
        print("db_421_trak", [dict(r) for r in rows])
    html, _ = fetch_player_history(RC_ID)
    parsed = parse_player_history(html)
    tail = [
        {
            "date": s.get("observed_at"),
            "rating": s.get("rc_rating"),
            "event": (s.get("event_name") or "")[:60],
        }
        for s in (parsed[-5:] if parsed else [])
    ]
    print("live_rc_tail", tail)


if __name__ == "__main__":
    main()
