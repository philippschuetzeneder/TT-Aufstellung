from sqlalchemy import text
from app.db import SessionLocal

with SessionLocal() as db:
    rows = db.execute(
        text(
            """
        SELECT substring(m.match_date from 1 for 10) AS d,
               m.home_team, m.away_team, m.team_result, m.external_id,
               (SELECT COUNT(*) FROM match_players mp
                WHERE mp.match_id = m.id AND mp.side = 'home'
                  AND mp.external_player_id IS NOT NULL) AS hc,
               (SELECT COUNT(*) FROM match_players mp
                WHERE mp.match_id = m.id AND mp.side = 'away'
                  AND mp.external_player_id IS NOT NULL) AS ac
        FROM xttv_matches m
        WHERE m.league LIKE '421%' AND m.season = '2026/2027'
          AND m.team_result IS NOT NULL
          AND m.team_result ~ '^\\s*[0-9]+\\s*:\\s*[0-9]+\\s*$'
        ORDER BY to_date(substring(m.match_date from 1 for 10), 'DD.MM.YYYY'), m.id
        """
        ),
    ).mappings().all()

for r in rows:
    print(
        f"{r['d']} {r['home_team']} vs {r['away_team']} "
        f"{r['team_result']} players {r['hc']}/{r['ac']} meid={r['external_id']}"
    )
