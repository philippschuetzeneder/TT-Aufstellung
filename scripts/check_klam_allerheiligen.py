"""Inspect Klam 1 vs Allerheiligen 1 lineup import in DB."""
from sqlalchemy import text
from app.db import SessionLocal

with SessionLocal() as db:
    m = db.execute(
        text(
            """
        SELECT m.id, m.external_id AS meid, m.match_date, m.team_result, m.league, m.season,
               m.home_team, m.away_team
        FROM xttv_matches m
        WHERE m.season = '2026/2027'
          AND m.home_team ILIKE 'Klam%'
          AND m.away_team ILIKE 'Allerheiligen%'
        """
        ),
    ).mappings().first()
    if not m:
        print("Kein Match Klam vs Allerheiligen in 2026/2027 gefunden.")
        raise SystemExit(1)

    print("Match:", m["home_team"], "vs", m["away_team"])
    print("Datum:", m["match_date"], "Ergebnis:", m["team_result"])
    print("Liga:", m["league"], "MEID:", m["meid"], "DB id:", m["id"])

    players = db.execute(
        text(
            """
        SELECT mp.side, mp.position, mp.name, mp.external_player_id, mp.id AS row_id
        FROM match_players mp
        WHERE mp.match_id = :mid
        ORDER BY mp.side, mp.position NULLS LAST, mp.name
        """
        ),
        {"mid": m["id"]},
    ).mappings().all()

    print("\nmatch_players (", len(players), " Zeilen):")
    for p in players:
        print(
            f"  {p['side']:4} pos={p['position']!s:4} ext_id={p['external_player_id']!s:8} {p['name']}"
        )

    home = [p for p in players if p["side"] == "home"]
    away = [p for p in players if p["side"] == "away"]
    print(f"\nHeim: {len(home)} mit external_player_id: {sum(1 for p in home if p['external_player_id'])}")
    print(f"Gast: {len(away)} mit external_player_id: {sum(1 for p in away if p['external_player_id'])}")

    raw = db.execute(
        text(
            "SELECT id, fetched_at, length(content) AS len FROM raw_source_documents "
            "WHERE source = 'xttv' AND external_id = :meid"
        ),
        {"meid": str(m["meid"])},
    ).mappings().first()
    print("\nRaw XTTV document:", dict(raw) if raw else "FEHLT")

    games = db.execute(
        text(
            """
        SELECT game_type, COUNT(*) FROM match_games WHERE match_id = :mid GROUP BY game_type
        """
        ),
        {"mid": m["id"]},
    ).all()
    print("match_games:", dict(games))
